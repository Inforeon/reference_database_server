from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository
from docsearch.server.dependencies import get_config
from docsearch.server.schemas import (
    AddTextbookRequest,
    ChapterContentResponse,
    ChapterResponse,
    TextbookUploadResponse,
)

router = APIRouter(prefix="/api/documents", tags=["textbooks"])


@router.post("/textbooks/add", response_model=TextbookUploadResponse)
async def add_textbook(
    body: AddTextbookRequest,
    config = Depends(get_config),
) -> TextbookUploadResponse:
    """Add a textbook to the index."""
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
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


@router.post("/textbooks/upload", response_model=TextbookUploadResponse)
async def upload_textbook(
    directory: str = "",
    filename: str | None = None,
    extra_metadata: str | None = None,
    file: UploadFile = File(...),
    config = Depends(get_config),
) -> TextbookUploadResponse:
    """Upload a textbook and index it automatically.

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
        indexer = Indexer(repo)
        doc = indexer.add_file(
            str(target_path),
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
                start_page=ch.start_page,
                end_page=ch.end_page,
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
            start_page=chapter.start_page,
            end_page=chapter.end_page,
            metadata=chapter.combined_metadata(doc),
            full_text=chapter.full_text,
        )
    finally:
        repo.close()
