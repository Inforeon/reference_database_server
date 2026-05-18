from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from docsearch.core.repository import Repository
from docsearch.server.dependencies import get_config
from docsearch.server.schemas import DirectoryListingResponse, FileSystemEntry

router = APIRouter(prefix="/api/fs", tags=["filesystem"])


@router.get("", response_model=DirectoryListingResponse)
async def list_directory(
    path: str = Query("", description="Directory path relative to database home"),
    config = Depends(get_config),
) -> DirectoryListingResponse:
    """List indexed contents of a directory within the index.

    Returns file entries for documents that live directly under the given
    directory, and directory entries inferred from nested document paths.
    Directory-type textbooks appear as directories with a ``document_id``.
    """
    # Guard against path traversal outside the database home.
    root = Path(config.home).resolve()
    target = (root / path).resolve()

    if not str(target).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Path escapes database home")

    # Pass the relative directory path (stored paths are relative to home)
    rel_dir = str(target.relative_to(root)) if path else ""

    repo = Repository(str(config.db_path))
    try:
        data = repo.list_directory(rel_dir)
    finally:
        repo.close()

    file_entries = [
        FileSystemEntry(name=e["name"], type=e["type"], document_id=e["document_id"])
        for e in data["entries"]
    ]
    dir_entries = [
        FileSystemEntry(name=d["name"], type=d["type"], document_id=d.get("document_id"))
        for d in data["directories"]
    ]

    return DirectoryListingResponse(
        path=path,
        entries=file_entries,
        directories=dir_entries,
    )
