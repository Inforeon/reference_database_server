from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from docsearch.core.handlers import _generate_bibtex_from_metadata
from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository
from docsearch.server.dependencies import get_config
from docsearch.server.schemas import (
    ContentResponse,
    DocumentResponse,
    MetaPatch,
    MoveDocumentRequest,
    MoveDocumentResponse,
)

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


@router.get("/{doc_id}/bibtex")
async def get_bibtex(
    doc_id: int,
    config = Depends(get_config),
) -> dict:
    """Export BibTeX for a research paper."""
    repo = Repository(str(config.db_path))
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

    repo = Repository(str(config.db_path))
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        old_path = doc.path

        # Also validate the source is inside the database home
        source_p = Path(old_path).resolve()
        if not str(source_p).startswith(str(root)):
            raise HTTPException(
                status_code=400,
                detail="Source document is outside the database home.",
            )

        indexer = Indexer(repo)
        new_doc = indexer.move_file(old_path, str(dest_p))
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
