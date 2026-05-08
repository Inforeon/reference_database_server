from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from docsearch.core.repository import Repository
from docsearch.server.dependencies import get_config
from docsearch.server.schemas import ContentResponse, DocumentResponse, MetaPatch

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: int,
    config = Depends(get_config),
) -> DocumentResponse:
    """Get a document by its internal ID."""
    repo = Repository(str(config.db_path))
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
    repo = Repository(str(config.db_path))
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
    repo = Repository(str(config.db_path))
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        if not Path(doc.path).is_file():
            raise HTTPException(status_code=404, detail="File not found on disk")
        return FileResponse(
            path=doc.path,
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
    repo = Repository(str(config.db_path))
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
    config = Depends(get_config),
) -> dict:
    """Get the sidecar metadata for a document."""
    repo = Repository(str(config.db_path))
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return doc.sidecar_metadata
    finally:
        repo.close()
