from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository
from docsearch.server.schemas import (
    AddFileRequest,
    IndexStats,
    RemoveFileRequest,
    ScanRequest,
)

router = APIRouter(prefix="/api/index", tags=["index"])


def get_db_path() -> str:
    import os

    return os.environ.get("DOCSEARCH_DB", os.path.expanduser("~/.local/share/docsearch/docsearch.db"))


@router.post("/scan", response_model=IndexStats)
async def scan_dir(
    body: ScanRequest,
    db_path: str = Depends(get_db_path),
) -> IndexStats:
    """Scan a directory tree and sync the index."""
    repo = Repository(db_path)
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
    db_path: str = Depends(get_db_path),
) -> dict | None:
    """Add a single file to the index."""
    repo = Repository(db_path)
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
    db_path: str = Depends(get_db_path),
) -> dict:
    """Remove a file from the index."""
    repo = Repository(db_path)
    try:
        indexer = Indexer(repo)
        found = indexer.remove_file(body.filepath)
        return {"removed": found}
    finally:
        repo.close()
