from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from docsearch.core.handlers import _generate_bibtex_from_metadata
from docsearch.core.indexer import Indexer
from docsearch.core.models import Document
from docsearch.core.repository import Repository
from docsearch.server.dependencies import get_config
from docsearch.server.schemas import (
    AddGenericReferenceRequest,
    ContentResponse,
    DocumentResponse,
    MetaPatch,
    MoveDocumentRequest,
    MoveDocumentResponse,
    UploadResponse,
)

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


@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: int,
    config = Depends(get_config),
) -> DocumentResponse:
    """Get a document by its internal ID."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
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
    finally:
        repo.close()


@router.get("/{doc_id}/content", response_model=ContentResponse)
async def get_content(
    doc_id: int,
    config = Depends(get_config),
) -> ContentResponse:
    """Get the extracted text content of a document."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return ContentResponse(
            id=doc.id,
            path=doc.path,
            filename=doc.filename,
            content=doc.full_text,
        )
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
    """Update a key in the sidecar metadata for a document."""
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        sidecar = config.home / doc.path + ".meta.json"
        data = {}
        if sidecar.is_file():
            with open(sidecar, "r") as f:
                data = json.load(f)

        data[body.key] = body.value

        with open(sidecar, "w") as f:
            json.dump(data, f, indent=2)

        # Re-index to pick up new sidecar
        indexer = Indexer(repo, config.home)
        indexer.add_file(doc.path)

        return {"updated": True, "key": body.key}
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

        # Preserve the sidecar — do NOT delete it
        sidecar_path = Path(str(abs_path) + ".meta.json")

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
