from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from docsearch.core.repository import Repository
from docsearch.server.schemas import DocumentResponse, MetaPatch

router = APIRouter(prefix="/api/documents", tags=["documents"])


def get_db_path() -> str:
    return os.environ.get("DOCSEARCH_DB", os.path.expanduser("~/.local/share/docsearch/docsearch.db"))


@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: int,
    db_path: str = Depends(get_db_path),
) -> DocumentResponse:
    """Get a document by its internal ID."""
    repo = Repository(db_path)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return DocumentResponse(
            path=doc.path,
            filename=doc.filename,
            directory=doc.directory,
            extension=doc.extension,
            size=doc.size,
            mtime=doc.mtime,
            metadata=doc.combined_metadata,
            indexed_at=doc.indexed_at,
        )
    finally:
        repo.close()


@router.patch("/{doc_id}/meta")
async def patch_meta(
    doc_id: int,
    body: MetaPatch,
    db_path: str = Depends(get_db_path),
) -> dict:
    """Update a key in the sidecar metadata for a document."""
    repo = Repository(db_path)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        sidecar = Path(str(doc.path) + ".meta.json")
        data = {}
        if sidecar.is_file():
            with open(sidecar, "r") as f:
                data = json.load(f)

        data[body.key] = body.value

        with open(sidecar, "w") as f:
            json.dump(data, f, indent=2)

        # Re-index to pick up new sidecar
        from docsearch.core.indexer import Indexer

        indexer = Indexer(repo)
        indexer.add_file(doc.path)

        return {"updated": True, "key": body.key}
    finally:
        repo.close()


@router.get("/{doc_id}/meta")
async def get_meta(
    doc_id: int,
    db_path: str = Depends(get_db_path),
) -> dict:
    """Get the sidecar metadata for a document."""
    repo = Repository(db_path)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return doc.sidecar_metadata
    finally:
        repo.close()
