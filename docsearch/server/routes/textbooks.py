from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository
from docsearch.server.dependencies import get_config
from docsearch.server.schemas import AddTextbookRequest, TextbookUploadResponse

router = APIRouter(prefix="/api/textbooks", tags=["textbooks"])


@router.post("/add", response_model=TextbookUploadResponse)
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


@router.post("/upload", response_model=TextbookUploadResponse)
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
