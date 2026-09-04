from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from docsearch.core.handlers import _generate_bibtex_from_metadata
from docsearch.core.indexer import Indexer
from docsearch.core.models import Document, Supplement
from docsearch.core.repository import Repository
from docsearch.core import slicing
from docsearch.server.dependencies import get_config
from docsearch.server.schemas import (
    AddGenericReferenceRequest,
    ContentResponse,
    CompactDocumentResponse,
    DocumentResponse,
    MetaPatch,
    MoveDocumentRequest,
    MoveDocumentResponse,
    SectionContentResponse,
    SectionInfo,
    SectionListResponse,
    SetSectionRequest,
    SupplementContentResponse,
    SupplementListResponse,
    SupplementResponse,
    UploadResponse,
    _extract_title,
    _extract_year,
    _format_author_slim,
)
from docsearch.core import slicing

router = APIRouter(prefix="/api/documents", tags=["documents"])

# Linux/ext4 limit for a single filename component (not full path).
_MAX_FILENAME_LENGTH = 255


def _validate_filename_length(name: str) -> None:
    """Raise 400 if any path component exceeds the OS filename length limit."""
    for part in Path(name).parts:
        if len(part) > _MAX_FILENAME_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Filename too long: '{part}' ({len(part)} chars). "
                    f"Maximum allowed is {_MAX_FILENAME_LENGTH} characters."
                ),
            )


@router.post("/reference", response_model=UploadResponse)
async def add_generic_reference(
    body: AddGenericReferenceRequest,
    config = Depends(get_config),
) -> UploadResponse:
    """Register a generic document reference (metadata-only, no associated file).

    Creates a document with ``source_type='reference'`` and ``document_type='generic'``
    containing only the supplied metadata. The ``filepath`` is used for grouping within
    the database home; if a file is later placed at that path, a normal add_file upsert
    will enrich the entry.
    """
    from typing import Any

    meta: dict[str, Any] = dict(body.extra_metadata or {})
    if body.title:
        meta["title"] = body.title
    if body.author:
        meta["author"] = body.author
    if body.subject:
        meta["subject"] = body.subject
    if body.keywords:
        meta["keywords"] = body.keywords
    if body.url:
        meta["url"] = body.url

    # Resolve filepath relative to database home
    filepath = body.filepath or ""

    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        doc = indexer.add_reference(
            filepath,
            document_type="generic",
            extra_metadata=meta or None,
        )
        if not doc:
            raise HTTPException(status_code=500, detail="Failed to create reference.")
        indexed = repo.get(doc.path)
        return UploadResponse(
            id=indexed.id,
            path=indexed.path,
            filename=indexed.filename,
        )
    finally:
        repo.close()


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    directory: str = "",
    filename: str | None = None,
    extra_metadata: str | None = None,
    file: UploadFile = File(...),
    config = Depends(get_config),
) -> UploadResponse:
    """Upload a generic document and index it automatically.

    Saves the file relative to the database home with strict path-traversal
    protection. ``extra_metadata`` is a JSON-encoded dict of additional key/value
    pairs merged into sidecar metadata.
    """
    meta: dict = {}
    if extra_metadata:
        try:
            meta = json.loads(extra_metadata)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="extra_metadata must be valid JSON.")

    root = config.home
    target_dir = root / directory if directory else root
    target_dir = target_dir.resolve()
    if not str(target_dir).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Directory must be within the database home.")

    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {target_dir}")

    name = filename if filename else file.filename or "uploaded"
    _validate_filename_length(name)
    target_path = target_dir / name

    if not str(target_path.resolve()).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Filename must not contain path separators.")

    with open(target_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        rel_target = str(target_path.relative_to(config.home))
        doc = indexer.add_file(rel_target, document_type="generic", extra_metadata=meta or None)
        if not doc:
            raise HTTPException(status_code=500, detail="Failed to index uploaded file.")
        indexed = repo.get(doc.path)
        return UploadResponse(
            id=indexed.id,
            path=indexed.path,
            filename=indexed.filename,
        )
    finally:
        repo.close()


@router.get("/{doc_id}", response_model=DocumentResponse | CompactDocumentResponse)
async def get_document(
    doc_id: int,
    verbose: bool = Query(False, description="Include full metadata (default: compact)"),
    config = Depends(get_config),
) -> DocumentResponse | CompactDocumentResponse:
    """Get a document by its internal ID.

    By default, returns compact metadata with slim author string and year.
    Set ``verbose=true`` for full combined metadata.
    """
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        if verbose:
            return _doc_to_response(doc)

        # Compact response
        meta = doc.combined_metadata
        chapter_count = None
        if doc.document_type == "textbook":
            chapters = repo.get_chapters(doc.id)
            chapter_count = len(chapters) if chapters else 0

        return CompactDocumentResponse(
            id=doc.id,
            path=doc.path,
            document_type=doc.document_type,
            source_type=doc.source_type,
            title=_extract_title(meta),
            author=_format_author_slim(meta),
            year=_extract_year(meta),
            chapter_count=chapter_count,
        )
    finally:
        repo.close()


@router.get("/{doc_id}/content", response_model=ContentResponse)
async def get_content(
    doc_id: int,
    lines: str | None = Query(None, description="Comma-separated line ranges, e.g. '0-99,200-299'"),
    config = Depends(get_config),
) -> ContentResponse:
    """Get the extracted text content of a document.

    Optionally slice by line numbers using the ``lines`` query parameter.
    Ranges are inclusive on both ends (e.g. ``"0-99,200-299"``).  A bare number
    selects a single line.
    """
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        content = doc.full_text
        if lines:
            text_lines = slicing.split_lines(doc.full_text)
            content = slicing.slice_lines(text_lines, lines)

        return ContentResponse(
            id=doc.id,
            path=doc.path,
            filename=doc.filename,
            content=content,
        )
    finally:
        repo.close()


# ── Section endpoints ───────────────────────────────────────────────

def _reject_directory_source(doc: Document) -> None:
    """Raise 400 if the document is a directory-type (no full_text to slice)."""
    if doc.source_type == "directory":
        raise HTTPException(
            status_code=400,
            detail="Sections are not supported for directory-type documents.",
        )


@router.get("/{doc_id}/sections", response_model=SectionListResponse)
async def list_sections(
    doc_id: int,
    config = Depends(get_config),
) -> SectionListResponse:
    """List document sections with line ranges and counts."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        _reject_directory_source(doc)

        text_lines = slicing.split_lines(doc.full_text)
        sections_map = slicing.get_sections_map(doc.combined_metadata)

        info_list: list[SectionInfo] = []
        for sec in sections_map:
            if sec["end"] is not None:
                line_count = sec["end"] - sec["start"] + 1  # inclusive bounds
            else:
                line_count = len(text_lines) - sec["start"]
            if line_count < 0:
                line_count = 0
            info_list.append(SectionInfo(
                index=sec["index"],
                name=sec["name"],
                start=sec["start"],
                end=sec["end"],
                line_count=line_count,
            ))

        return SectionListResponse(
            id=doc.id,
            path=doc.path,
            sections=info_list,
        )
    finally:
        repo.close()


@router.get("/{doc_id}/sections/{section_index}", response_model=SectionContentResponse)
async def get_section(
    doc_id: int,
    section_index: int,
    config = Depends(get_config),
) -> SectionContentResponse:
    """Get the text content of a specific section by index."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        _reject_directory_source(doc)

        sections_map = slicing.get_sections_map(doc.combined_metadata)
        sec = next((s for s in sections_map if s["index"] == section_index), None)
        if sec is None:
            raise HTTPException(
                status_code=404,
                detail=f"Section {section_index} not found. "
                       f"Available: {[s['index'] for s in sections_map]}",
            )

        text_lines = slicing.split_lines(doc.full_text)
        content = slicing.get_section_text(text_lines, sec)

        return SectionContentResponse(
            id=doc.id,
            path=doc.path,
            section_index=sec["index"],
            section_name=sec["name"],
            start=sec["start"],
            end=sec["end"],
            content=content,
        )
    finally:
        repo.close()


@router.post("/{doc_id}/sections", response_model=SectionListResponse)
async def add_section(
    doc_id: int,
    body: SetSectionRequest,
    config = Depends(get_config),
) -> SectionListResponse:
    """Add a new section to a document. Index is auto-incremented."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        _reject_directory_source(doc)

        indexer = Indexer(repo, config.home)
        current = slicing.get_sections_map(doc.combined_metadata)
        new_index = max((s["index"] for s in current), default=-1) + 1

        sections_dict = doc.sidecar_metadata.get("sections", {}) or {}
        sections_dict[str(new_index)] = {
            "name": body.name,
            "start": body.start,
            "end": body.end,
        }
        indexer.set_metadata_key(doc_id, "sections", sections_dict)

        # Reload for response
        updated = repo.get_by_id(doc_id)
        text_lines = slicing.split_lines(updated.full_text)
        sections_map = slicing.get_sections_map(updated.combined_metadata)

        info_list: list[SectionInfo] = []
        for sec in sections_map:
            if sec["end"] is not None:
                line_count = sec["end"] - sec["start"] + 1  # inclusive bounds
            else:
                line_count = len(text_lines) - sec["start"]
            if line_count < 0:
                line_count = 0
            info_list.append(SectionInfo(
                index=sec["index"],
                name=sec["name"],
                start=sec["start"],
                end=sec["end"],
                line_count=line_count,
            ))

        return SectionListResponse(
            id=updated.id,
            path=updated.path,
            sections=info_list,
        )
    finally:
        repo.close()


@router.delete("/{doc_id}/sections/{section_index}", status_code=204)
async def delete_section(
    doc_id: int,
    section_index: int,
    config = Depends(get_config),
) -> None:
    """Delete a section by index. Remaining sections are re-indexed from 0."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        _reject_directory_source(doc)

        sections_dict = doc.sidecar_metadata.get("sections")
        if not sections_dict or str(section_index) not in sections_dict:
            raise HTTPException(
                status_code=404,
                detail=f"Section {section_index} not found.",
            )

        # Remove and reindex
        del sections_dict[str(section_index)]
        reindexed = slicing.reindex_sections(sections_dict)

        indexer = Indexer(repo, config.home)
        if reindexed:
            indexer.set_metadata_key(doc_id, "sections", reindexed)
        else:
            indexer.delete_metadata_key(doc_id, "sections")

    finally:
        repo.close()


@router.get("/{doc_id}/file")
async def get_file(
    doc_id: int,
    config = Depends(get_config),
) -> FileResponse:
    """Download the original file for a document."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        abs_path = config.home / doc.path
        if not abs_path.is_file():
            raise HTTPException(status_code=404, detail="File not found on disk")
        return FileResponse(
            path=str(abs_path),
            filename=doc.filename,
            media_type=f"application/{doc.extension}",
        )
    finally:
        repo.close()


@router.patch("/{doc_id}/meta")
async def patch_meta(
    doc_id: int,
    body: MetaPatch,
    config = Depends(get_config),
) -> dict:
    """Update a key in the sidecar metadata for a document.

    Writes the database column and the ``.meta.json`` file together, without
    re-extracting the document.  Works for reference-only entries, which have
    no file on disk to re-index.
    """
    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        if not indexer.set_metadata_key(doc_id, body.key, body.value):
            raise HTTPException(status_code=404, detail="Document not found")

        updated = repo.get_by_id(doc_id)
        sidecar = updated.sidecar_metadata if updated else {}
        return {"updated": True, "key": body.key, "metadata": sidecar}
    finally:
        repo.close()


@router.get("/{doc_id}/meta")
async def get_meta(
    doc_id: int,
    config = Depends(get_config),
) -> dict:
    """Get the sidecar metadata for a document."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return doc.sidecar_metadata
    finally:
        repo.close()


@router.get("/{doc_id}/bibtex")
async def get_bibtex(
    doc_id: int,
    config = Depends(get_config),
) -> dict:
    """Export BibTeX for a research paper."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        if doc.document_type != "paper":
            raise HTTPException(
                status_code=400,
                detail=f"Document is not a paper (type={doc.document_type})",
            )

        bibtex_str = doc.sidecar_metadata.get("bibtex")
        if not bibtex_str:
            # Fallback: generate from available metadata
            bibtex_str = _generate_bibtex_from_metadata(doc.combined_metadata)

        return {"id": doc.id, "bibtex": bibtex_str}
    finally:
        repo.close()


@router.post("/{doc_id}/move", response_model=MoveDocumentResponse)
async def move_document(
    doc_id: int,
    body: MoveDocumentRequest,
    config = Depends(get_config),
) -> MoveDocumentResponse:
    """Move a document to a new location within the database home.

    The destination path is resolved relative to the database home when
    relative, and must remain a descendant of the database home.
    Parent directories are created automatically.
    """
    root = config.home

    # Validate filename length before resolving paths
    _validate_filename_length(body.destination)

    # Resolve destination relative to database home
    dest_p = Path(body.destination)
    if dest_p.is_absolute():
        dest_p = dest_p.resolve()
    else:
        dest_p = (root / dest_p).resolve()

    # Enforce containment within database home
    if not str(dest_p).startswith(str(root)):
        raise HTTPException(
            status_code=400,
            detail="Destination must be within the database home.",
        )

    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        old_path = doc.path

        # Also validate the source is inside the database home
        source_p = (root / old_path).resolve()
        if not str(source_p).startswith(str(root)):
            raise HTTPException(
                status_code=400,
                detail="Source document is outside the database home.",
            )

        indexer = Indexer(repo, config.home)
        dest_rel = str(dest_p.relative_to(root))
        new_doc = indexer.move_file(old_path, dest_rel)
        if new_doc is None:
            raise HTTPException(status_code=404, detail="Source document not found in index")

        return MoveDocumentResponse(
            id=new_doc.id,
            old_path=old_path,
            new_path=new_doc.path,
            filename=new_doc.filename,
        )
    finally:
        repo.close()


@router.post("/{doc_id}/attach", response_model=DocumentResponse)
async def attach_file(
    doc_id: int,
    directory: str = "",
    filename: str | None = None,
    file: UploadFile = File(...),
    config = Depends(get_config),
) -> DocumentResponse:
    """Attach a physical file to a reference-only entry, converting it to source_type='file'.

    The existing metadata from the reference entry is preserved by merging it into
    the sidecar so it takes precedence over any conflicting metadata extracted from the
    uploaded file.
    """
    root = config.home

    # Resolve destination path
    target_dir = root / directory if directory else root
    target_dir = target_dir.resolve()
    if not str(target_dir).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Directory must be within the database home.")
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {target_dir}")

    name = filename if filename else file.filename or "attached"
    _validate_filename_length(name)
    target_path = target_dir / name

    if not str(target_path.resolve()).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Filename must not contain path separators.")

    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        if doc.source_type != "reference":
            raise HTTPException(
                status_code=400,
                detail=f"Document is not a reference entry (source_type={doc.source_type!r}). "
                       "Only reference entries can have a file attached.",
            )

        # Save the uploaded file to disk
        with open(target_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        rel_target = str(target_path.relative_to(root))

        # Delegate to indexer: rename DB path → write sidecar → extract
        indexer = Indexer(repo, config.home)
        new_doc = indexer.attach_file(
            rel_target,
            doc_id,
            document_type=doc.document_type,
            existing_metadata=doc.combined_metadata or None,
        )
        if new_doc is None:
            raise HTTPException(status_code=500, detail="Failed to index attached file.")

        # Update source_type to "file"
        repo.update_document(doc_id, source_type="file")

        new_doc = repo.get_by_id(doc_id)
        return _doc_to_response(new_doc)
    finally:
        repo.close()


@router.post("/{doc_id}/detach", response_model=DocumentResponse)
async def detach_file(
    doc_id: int,
    config = Depends(get_config),
) -> DocumentResponse:
    """Detach the physical file from a document, converting it to source_type='reference'.

    Deletes the main file but preserves the sidecar (.meta.json) so user-editable
    metadata survives. Clears full_text and extracted_metadata in the database.
    """
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        if doc.source_type == "reference":
            raise HTTPException(
                status_code=400,
                detail="Document is already a reference entry (no file to detach).",
            )
        if doc.source_type == "directory":
            raise HTTPException(
                status_code=400,
                detail="Cannot detach a directory-type document. "
                       "This operation is only supported for file-backed documents.",
            )

        abs_path = config.home / doc.path

        # Delete the main file
        if abs_path.is_file():
            abs_path.unlink()

        # Preserve the sidecar (<path>.meta.json) — do NOT delete it.  It now
        # backs a reference-only entry, where it is the durable copy of the
        # document's metadata.

        # Clear extractable content in the DB (no file → nothing to extract)
        repo.update_document(
            doc_id,
            source_type="reference",
            full_text="",
            extracted_metadata={},
        )

        new_doc = repo.get_by_id(doc_id)
        return _doc_to_response(new_doc)
    finally:
        repo.close()


def _doc_to_response(doc: Document) -> DocumentResponse:
    """Convert a Document model to a DocumentResponse."""
    return DocumentResponse(
        id=doc.id,
        path=doc.path,
        filename=doc.filename,
        directory=doc.directory,
        extension=doc.extension,
        document_type=doc.document_type,
        source_type=doc.source_type,
        size=doc.size,
        mtime=doc.mtime,
        metadata=doc.combined_metadata,
        indexed_at=doc.indexed_at,
    )


# ── Supplement endpoints (must be before catch-all /{doc_id}) ────

@router.get("/{doc_id}/supplements", response_model=SupplementListResponse)
async def list_supplements(doc_id: int, config=Depends(get_config)) -> SupplementListResponse:
    """List all supplements for a directory-type paper."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        if doc.document_type != "paper":
            raise HTTPException(status_code=400, detail="Document is not a paper")

        supplements = repo.get_supplements(doc_id)
        return SupplementListResponse(
            id=doc_id, path=doc.path,
            supplements=[SupplementResponse(id=s.id, paper_id=s.paper_id, supplement_index=s.supplement_index,
                                            title=s.title, file_path=s.file_path, metadata=s.metadata) for s in supplements],
        )
    finally:
        repo.close()


@router.get("/{doc_id}/supplements/{supplement_index}", response_model=SupplementContentResponse)
async def get_supplement(doc_id: int, supplement_index: int,
                         lines: str | None = Query(None), section: int | None = Query(None),
                         config=Depends(get_config)) -> SupplementContentResponse:
    """Get a supplement by index, optionally sliced by lines or section."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        sup = repo.get_supplement(doc_id, supplement_index)
        if not sup:
            raise HTTPException(status_code=404, detail="Supplement not found")

        content = sup.full_text
        if section is not None:
            sec_list = slicing.get_sections_map(sup.metadata)
            sec = next((s for s in sec_list if s["index"] == section), None)
            if not sec:
                raise HTTPException(status_code=404, detail=f"Section {section} not found")
            content = slicing.get_section_text(slicing.split_lines(content), sec)
        elif lines:
            content = slicing.slice_lines(slicing.split_lines(content), lines)

        return SupplementContentResponse(id=sup.id, paper_id=sup.paper_id, supplement_index=sup.supplement_index,
                                         title=sup.title, file_path=sup.file_path, metadata=sup.metadata, content=content)
    finally:
        repo.close()


@router.post("/{doc_id}/supplements/upload", response_model=SupplementResponse)
async def upload_supplement(doc_id: int, file: UploadFile = File(...),
                            directory: str = Query(""), filename: str | None = Query(None),
                            config=Depends(get_config)) -> SupplementResponse:
    """Upload a supplement file. Auto-converts file-type papers to directory-type."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    target_dir = Path(config.home) / directory if directory else Path(config.home)
    target_dir = target_dir.resolve()
    if not str(target_dir).startswith(str(config.home)):
        raise HTTPException(status_code=400, detail="Directory must be within the database home")
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {target_dir}")

    sup_filename = filename or file.filename
    sup_path = target_dir / sup_filename
    with open(sup_path, "wb") as f:
        f.write(await file.read())

    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        rel_path = str(sup_path.relative_to(config.home))
        doc = indexer.convert_to_directory(doc_id, rel_path, sup_filename)
        if not doc:
            raise HTTPException(status_code=500, detail="Failed to attach supplement")
        supplements = repo.get_supplements(doc_id)
        if not supplements:
            raise HTTPException(status_code=500, detail="Supplement was not indexed")
        sup = supplements[-1]
        return SupplementResponse(id=sup.id, paper_id=sup.paper_id, supplement_index=sup.supplement_index,
                                  title=sup.title, file_path=sup.file_path, metadata=sup.metadata)
    finally:
        repo.close()


@router.delete("/{doc_id}/supplements/{supplement_index}")
async def delete_supplement(doc_id: int, supplement_index: int, config=Depends(get_config)) -> dict:
    """Delete a supplement by index."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        if doc.source_type != "directory":
            raise HTTPException(status_code=400, detail="Document is not a directory-type paper")
        sup = repo.get_supplement(doc_id, supplement_index)
        if not sup:
            raise HTTPException(status_code=404, detail="Supplement not found")

        repo.delete_supplement_by_id(sup.id)
        from docsearch.core.sidecars import sidecar_path
        dir_p = Path(config.home) / doc.path
        if sup.file_path and (dir_p / sup.file_path).is_file():
            (dir_p / sup.file_path).unlink()

        from docsearch.core.sidecars import load_sidecar, write_sidecar
        sidecar = sidecar_path(dir_p, "directory")
        meta = load_sidecar(sidecar)
        supplements = meta.get("supplements", {})
        if str(supplement_index) in supplements:
            del supplements[str(supplement_index)]
            reindexed = {}
            for new_i, (_, val) in enumerate(sorted(supplements.items(), key=lambda x: int(x[0]))):
                reindexed[str(new_i)] = val
            meta["supplements"] = reindexed
            write_sidecar(sidecar, meta)

        return {"deleted": True, "supplement_index": supplement_index}
    finally:
        repo.close()


@router.get("/{doc_id}/supplements/{supplement_index}/sections", response_model=SectionListResponse)
async def list_supplement_sections(doc_id: int, supplement_index: int, config=Depends(get_config)) -> SectionListResponse:
    """List sections for a supplement."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        sup = repo.get_supplement(doc_id, supplement_index)
        if not sup:
            raise HTTPException(status_code=404, detail="Supplement not found")

        sections = slicing.get_sections_map(sup.metadata)
        lines_all = slicing.split_lines(sup.full_text)
        section_infos = [SectionInfo(index=sec["index"], name=sec["name"], start=sec["start"], end=sec["end"],
                                     line_count=(sec["end"] if sec["end"] is not None else len(lines_all)-1) - sec["start"] + 1)
                         for sec in sections]
        return SectionListResponse(id=sup.id, path=f"{doc.path} :: {sup.title}", sections=section_infos)
    finally:
        repo.close()


@router.post("/{doc_id}/supplements/{supplement_index}/sections")
async def add_supplement_section(doc_id: int, supplement_index: int, request: SetSectionRequest,
                                 config=Depends(get_config)) -> dict:
    """Add a section to a supplement."""
    repo = Repository(str(config.db_path), config.home)
    try:
        sup = repo.get_supplement(doc_id, supplement_index)
        if not sup:
            raise HTTPException(status_code=404, detail="Supplement not found")
        meta = dict(sup.metadata)
        current_sections = meta.get("sections", {})
        existing_indices = [int(k) for k in current_sections.keys() if k.isdigit()]
        new_idx = max(existing_indices, default=-1) + 1
        current_sections[str(new_idx)] = {"name": request.name, "start": request.start, "end": request.end}
        meta["sections"] = current_sections
        repo.update_supplement_metadata(sup.id, meta)
        return {"added": True, "section_index": new_idx}
    finally:
        repo.close()


@router.get("/{doc_id}/supplements/{supplement_index}/sections/{section_index}", response_model=SectionContentResponse)
async def get_supplement_section(doc_id: int, supplement_index: int, section_index: int,
                                 config=Depends(get_config)) -> SectionContentResponse:
    """Get a section from a supplement."""
    repo = Repository(str(config.db_path), config.home)
    try:
        sup = repo.get_supplement(doc_id, supplement_index)
        if not sup:
            raise HTTPException(status_code=404, detail="Supplement not found")
        sections = slicing.get_sections_map(sup.metadata)
        sec = next((s for s in sections if s["index"] == section_index), None)
        if not sec:
            raise HTTPException(status_code=404, detail=f"Section {section_index} not found")
        content = slicing.get_section_text(slicing.split_lines(sup.full_text), sec)
        return SectionContentResponse(id=sup.id, path=str(doc_id), section_index=sec["index"],
                                      section_name=sec["name"], start=sec["start"], end=sec["end"], content=content)
    finally:
        repo.close()


@router.delete("/{doc_id}/supplements/{supplement_index}/sections/{section_index}")
async def delete_supplement_section(doc_id: int, supplement_index: int, section_index: int,
                                    config=Depends(get_config)) -> dict:
    """Delete a section from a supplement."""
    repo = Repository(str(config.db_path), config.home)
    try:
        sup = repo.get_supplement(doc_id, supplement_index)
        if not sup:
            raise HTTPException(status_code=404, detail="Supplement not found")
        meta = dict(sup.metadata)
        sections_dict = meta.get("sections", {})
        if str(section_index) not in sections_dict:
            raise HTTPException(status_code=404, detail=f"Section {section_index} not found")
        del sections_dict[str(section_index)]
        reindexed = slicing.reindex_sections(sections_dict)
        meta["sections"] = reindexed if reindexed else {}
        if not reindexed:
            meta.pop("sections", None)
        repo.update_supplement_metadata(sup.id, meta)
        return {"deleted": True, "section_index": section_index}
    finally:
        repo.close()
