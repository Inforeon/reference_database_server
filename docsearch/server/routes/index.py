from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository
from docsearch.server.dependencies import get_config
from docsearch.server.schemas import (
    AddFileRequest,
    IndexStats,
    RemoveFileRequest,
    ScanRequest,
)

router = APIRouter(prefix="/api/index", tags=["index"])


@router.post("/scan", response_model=IndexStats)
async def scan_dir(
    body: ScanRequest,
    config = Depends(get_config),
) -> IndexStats:
    """Scan a directory tree and sync the index.

    For document-type-specific behaviour (e.g. paper DOI resolution), prefer
    the dedicated ``/api/documents/papers`` or ``/api/documents/textbooks`` endpoints.
    """
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo, config.home)
        stats = indexer.scan_directory(body.dirpath, recursive=body.recursive, document_type=body.document_type, extra_metadata=body.extra_metadata or None)
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
    """Add a single file to the index.

    For document-type-specific behaviour (e.g. paper DOI resolution), prefer
    the dedicated ``/api/documents/papers`` or ``/api/documents/textbooks`` endpoints.
    """
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo, config.home)
        doc = indexer.add_file(body.filepath, document_type=body.document_type, extra_metadata=body.extra_metadata or None)
        if doc:
            indexed = repo.get(doc.path)
            return {
                "id": indexed.id if indexed else None,
                "path": doc.path,
                "filename": doc.filename,
                "document_type": doc.document_type,
            }
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
        indexer = Indexer(repo, config.home)
        found = indexer.remove_file(body.filepath)
        return {"removed": found}
    finally:
        repo.close()
