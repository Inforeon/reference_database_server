from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from docsearch.core.indexer import Indexer
from docsearch.core.models import Chapter
from docsearch.core.repository import Repository
from docsearch.extractors import load_extractors
from docsearch.server.dependencies import get_config
from docsearch.server.schemas import (
    AddTextbookReferenceRequest,
    AddTextbookRequest,
    ChapterContentResponse,
    ChapterResponse,
    TextbookUploadResponse,
)

router = APIRouter(prefix="/api/documents/textbooks", tags=["textbooks"])


@router.post("/reference", response_model=TextbookUploadResponse)
async def add_textbook_reference(
    body: AddTextbookReferenceRequest,
    config = Depends(get_config),
) -> TextbookUploadResponse:
    """Register a textbook reference (metadata-only, no associated file).

    Creates a document with ``source_type='reference'`` and ``document_type='textbook'``
    containing only the supplied metadata. The ``filepath`` is used for grouping within
    the database home; if a file is later placed at that path, a normal add_file upsert
    will enrich the entry.
    """
    meta: dict[str, Any] = dict(body.extra_metadata or {})
    if body.title:
        meta["title"] = body.title
    if body.author:
        meta["author"] = body.author
    if body.year:
        meta["year"] = body.year
    if body.publisher:
        meta["publisher"] = body.publisher
    if body.edition:
        meta["edition"] = body.edition
    if body.url:
        meta["url"] = body.url

    # Resolve filepath relative to database home
    filepath = body.filepath or ""

    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo, config.home)
        doc = indexer.add_reference(
            filepath,
            document_type="textbook",
            extra_metadata=meta or None,
        )
        if not doc:
            raise HTTPException(status_code=500, detail="Failed to create reference.")
        indexed = repo.get(doc.path)
        return TextbookUploadResponse(
            id=indexed.id,
            path=indexed.path,
            filename=indexed.filename,
        )
    finally:
        repo.close()


@router.post("/add", response_model=TextbookUploadResponse)
async def add_textbook(
    body: AddTextbookRequest,
    config = Depends(get_config),
) -> TextbookUploadResponse:
    """Add a textbook to the index."""
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo, config.home)
        extra_meta: dict[str, Any] = dict(body.extra_metadata or {})

        doc = indexer.add_file(
            body.filepath,
            document_type="textbook",
            extra_metadata=extra_meta or None,
        )
        if not doc:
            raise HTTPException(status_code=500, detail="Failed to index textbook.")
        indexed = repo.get(doc.path)
        return TextbookUploadResponse(
            id=indexed.id,
            path=indexed.path,
            filename=indexed.filename,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        repo.close()


@router.post("/upload", response_model=TextbookUploadResponse)
async def upload_textbook(
    directory: str = "",
    filename: str | None = None,
    extra_metadata: str | None = None,
    variant: str = Query("file", description="'file' for single-PDF textbook, 'directory' to create empty directory textbook"),
    file: UploadFile | None = File(None),
    config = Depends(get_config),
) -> TextbookUploadResponse:
    """Upload a textbook and index it automatically.

    When ``variant=directory`` (no file required), creates an empty directory
    at the specified path with a Document entry so chapters can be uploaded later.

    ``extra_metadata`` is a JSON-encoded dict of additional key/value pairs.
    """
    meta: dict[str, Any] = {}
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

    # Empty directory-type textbook creation
    if variant == "directory":
        if not target_dir.is_dir():
            raise HTTPException(status_code=400, detail=f"Directory does not exist: {target_dir}")

        name = filename if filename else "textbook"
        textbook_dir = target_dir / name
        textbook_dir.mkdir(parents=True, exist_ok=True)

        repo = Repository(str(config.db_path))
        try:
            indexer = Indexer(repo, config.home)
            rel_dir = str(textbook_dir.relative_to(config.home))
            doc = indexer.add_file(
                rel_dir,
                document_type="textbook",
                extra_metadata=meta or None,
            )
            if not doc:
                raise HTTPException(status_code=500, detail="Failed to register empty textbook directory.")
            indexed = repo.get(doc.path)
            return TextbookUploadResponse(
                id=indexed.id,
                path=indexed.path,
                filename=indexed.filename,
            )
        finally:
            repo.close()

    # Standard file-type textbook upload
    if file is None:
        raise HTTPException(status_code=400, detail="A file is required for variant='file'.")

    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {target_dir}")

    name = filename if filename else file.filename or "uploaded"
    target_path = target_dir / name

    if not str(target_path.resolve()).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Filename must not contain path separators.")

    with open(target_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo, config.home)
        rel_target = str(target_path.relative_to(config.home))
        doc = indexer.add_file(
            rel_target,
            document_type="textbook",
            extra_metadata=meta or None,
        )
        if not doc:
            raise HTTPException(status_code=500, detail="Failed to index uploaded textbook.")
        indexed = repo.get(doc.path)
        return TextbookUploadResponse(
            id=indexed.id,
            path=indexed.path,
            filename=indexed.filename,
        )
    finally:
        repo.close()


@router.get("/{doc_id}/chapters", response_model=list[ChapterResponse])
async def list_chapters(
    doc_id: int,
    config = Depends(get_config),
) -> list[ChapterResponse]:
    """List all chapters for a textbook. Returns 400 if not a textbook."""
    repo = Repository(str(config.db_path))
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found.")
        if doc.document_type != "textbook":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot list chapters: document {doc.filename!r} is type '{doc.document_type}', not 'textbook'.",
            )

        chapters = repo.get_chapters(doc_id)
        return [
            ChapterResponse(
                id=ch.id,
                textbook_id=ch.textbook_id,
                chapter_index=ch.chapter_index,
                title=ch.title,
                chapter_type=ch.chapter_type or "range",
                start_page=ch.start_page,
                end_page=ch.end_page,
                page_count=ch.page_count,
                file_path=ch.file_path,
                metadata=ch.combined_metadata(doc),
            )
            for ch in chapters
        ]
    finally:
        repo.close()


@router.get("/{doc_id}/chapters/{chapter_index}", response_model=ChapterContentResponse)
async def get_chapter(
    doc_id: int,
    chapter_index: int,
    config = Depends(get_config),
) -> ChapterContentResponse:
    """Get a specific chapter by index. Returns 400 if not a textbook, 404 if chapter missing."""
    repo = Repository(str(config.db_path))
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found.")
        if doc.document_type != "textbook":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot get chapter: document {doc.filename!r} is type '{doc.document_type}', not 'textbook'.",
            )

        chapter = repo.get_chapter(doc_id, chapter_index)
        if not chapter:
            raise HTTPException(status_code=404, detail=f"Chapter {chapter_index} not found.")

        return ChapterContentResponse(
            id=chapter.id,
            textbook_id=chapter.textbook_id,
            chapter_index=chapter.chapter_index,
            title=chapter.title,
            chapter_type=chapter.chapter_type or "range",
            start_page=chapter.start_page,
            end_page=chapter.end_page,
            page_count=chapter.page_count,
            file_path=chapter.file_path,
            metadata=chapter.combined_metadata(doc),
            full_text=chapter.full_text,
        )
    finally:
        repo.close()


@router.post("/{doc_id}/chapters/upload", response_model=ChapterResponse)
async def upload_chapter(
    doc_id: int,
    filename: str | None = None,
    chapter_index: int | None = Query(None, description="Explicit chapter index (auto-assign if omitted)"),
    file: UploadFile = File(...),
    config = Depends(get_config),
) -> ChapterResponse:
    """Upload a chapter file to a directory-type textbook.

    Saves the file into the textbook's directory and creates a corresponding
    chapter entry. If a file with the same name already exists, it is overwritten
    and the old chapter row is replaced.
    """
    repo = Repository(str(config.db_path))
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found.")
        if doc.document_type != "textbook":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot upload chapter: document {doc.filename!r} is type '{doc.document_type}', not 'textbook'.",
            )
        if doc.source_type != "directory":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot upload chapter: textbook {doc.filename!r} is variant '{doc.source_type}', not 'directory'. "
                       "Chapter uploads are only supported for directory-type textbooks.",
            )

        textbook_dir = config.home / doc.path
        if not textbook_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"Textbook directory does not exist: {textbook_dir}")

        # Determine target filename
        name = filename if filename else file.filename or "chapter"
        target_path = textbook_dir / name

        if not str(target_path.resolve()).startswith(str(textbook_dir)):
            raise HTTPException(status_code=400, detail="Filename must not contain path separators.")

        # If a file already exists at destination, remove its old chapter entry
        old_chapter = repo.get_chapter_by_file_path(doc_id, name)
        if old_chapter and target_path.exists():
            repo.delete_chapter_by_id(old_chapter.id)

        # Save the uploaded file (overwrites if exists)
        with open(target_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        # Auto-assign chapter_index if not provided
        if chapter_index is None:
            existing = repo.get_chapters(doc_id)
            used_indices = {ch.chapter_index for ch in existing}
            idx = 0
            while idx in used_indices:
                idx += 1
            chapter_index = idx

        # Extract text and metadata from the chapter file
        extractors = load_extractors()
        ext = target_path.suffix.lower().lstrip(".")
        extractor = extractors.get(ext)

        extracted_meta: dict[str, Any] = {}
        full_text = ""
        page_count: int | None = None

        if extractor:
            extracted_meta, full_text = extractor.extract(str(target_path))

            # Get page count for PDFs
            try:
                import fitz
                with fitz.open(str(target_path)) as pdf_doc:
                    page_count = len(pdf_doc)
            except Exception:
                pass

        title = name.replace(".pdf", "").replace("_", " ").replace("-", " ").title()

        chapter = Chapter(
            textbook_id=doc_id,
            chapter_index=chapter_index,
            title=title,
            chapter_type="file",
            start_page=None,
            end_page=None,
            page_count=page_count,
            file_path=name,
            metadata=extracted_meta,
            full_text=full_text,
        )
        repo.upsert_chapter(chapter)

        # Reload to get the assigned ID
        saved = repo.get_chapter(doc_id, chapter_index)
        if not saved:
            raise HTTPException(status_code=500, detail="Failed to save chapter.")

        return ChapterResponse(
            id=saved.id,
            textbook_id=saved.textbook_id,
            chapter_index=saved.chapter_index,
            title=saved.title,
            chapter_type=saved.chapter_type or "range",
            start_page=saved.start_page,
            end_page=saved.end_page,
            page_count=saved.page_count,
            file_path=saved.file_path,
            metadata=saved.combined_metadata(doc),
        )
    finally:
        repo.close()
