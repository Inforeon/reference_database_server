from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository
from docsearch.server.dependencies import get_config
from docsearch.server.schemas import (
    AddFileRequest,
    IndexStats,
    RemoveFileRequest,
    ScanRequest,
    UploadResponse,
)

router = APIRouter(prefix="/api/index", tags=["index"])


@router.post("/scan", response_model=IndexStats)
async def scan_dir(
    body: ScanRequest,
    config = Depends(get_config),
) -> IndexStats:
    """Scan a directory tree and sync the index."""
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        stats = indexer.scan_directory(body.dirpath, recursive=body.recursive)
        return IndexStats(**stats)
    except NotADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        repo.close()


@router.post("/add")
async def add_file(
    body: AddFileRequest,
    config = Depends(get_config),
) -> dict | None:
    """Add a single file to the index."""
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        doc = indexer.add_file(body.filepath)
        if doc:
            return {"path": doc.path, "filename": doc.filename}
        return None
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        repo.close()


@router.post("/remove")
async def remove_file(
    body: RemoveFileRequest,
    config = Depends(get_config),
) -> dict:
    """Remove a file from the index."""
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        found = indexer.remove_file(body.filepath)
        return {"removed": found}
    finally:
        repo.close()


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    directory: str = "",
    filename: str | None = None,
    file: UploadFile = File(...),
    config = Depends(get_config),
) -> UploadResponse:
    """Upload a file and index it automatically.

    The file is saved relative to the database home directory.
    If ``filename`` is not provided, the uploaded file's original name is used.
    """
    root = config.home
    target_dir = root / directory if directory else root

    # Resolve to prevent path traversal
    target_dir = target_dir.resolve()
    if not str(target_dir).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Directory must be within the database home.")

    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {target_dir}")

    name = filename if filename else file.filename or "uploaded"
    target_path = target_dir / name

    # Prevent path traversal on filename
    if not str(target_path.resolve()).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Filename must not contain path separators.")

    # Save file
    with open(target_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Index the file
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        doc = indexer.add_file(str(target_path))
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
