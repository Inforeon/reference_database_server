from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository
from docsearch.server.dependencies import get_config
from docsearch.server.schemas import AddPaperRequest, AddReferenceRequest, PaperUploadResponse

router = APIRouter(prefix="/api/documents/papers", tags=["papers"])


@router.post("/add", response_model=PaperUploadResponse)
async def add_paper(
    body: AddPaperRequest,
    config = Depends(get_config),
) -> PaperUploadResponse:
    """Add a research paper to the index.

    If ``doi`` is provided it will be embedded into the PDF before bibliographic
    extraction via pdf2bib.  Set ``skip_bib = true`` to bypass pdf2bib entirely.
    """
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        extra_meta: dict[str, Any] = dict(body.extra_metadata or {})
        if body.doi:
            extra_meta["doi"] = body.doi

        doc = indexer.add_file(
            body.filepath,
            document_type="paper",
            extra_metadata=extra_meta or None,
            skip_bib=body.skip_bib,
        )
        if not doc:
            raise HTTPException(status_code=500, detail="Failed to index paper.")
        indexed = repo.get(doc.path)
        return PaperUploadResponse(
            id=indexed.id,
            path=indexed.path,
            filename=indexed.filename,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        repo.close()


@router.post("/upload", response_model=PaperUploadResponse)
async def upload_paper(
    directory: str = "",
    filename: str | None = None,
    doi: str | None = None,
    skip_bib: bool = False,
    extra_metadata: str | None = None,
    file: UploadFile = File(...),
    config = Depends(get_config),
) -> PaperUploadResponse:
    """Upload a research paper and index it automatically.

    ``doi`` embeds a known DOI into the PDF before bibliographic extraction.
    ``skip_bib`` bypasses pdf2bib entirely (generates bibtex from available metadata).
    ``extra_metadata`` is a JSON-encoded dict of additional key/value pairs.
    """
    meta: dict[str, Any] = {}
    if extra_metadata:
        try:
            meta = json.loads(extra_metadata)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="extra_metadata must be valid JSON.")

    if doi:
        meta["doi"] = doi

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
            document_type="paper",
            extra_metadata=meta or None,
            skip_bib=skip_bib,
        )
        if not doc:
            raise HTTPException(status_code=500, detail="Failed to index uploaded paper.")
        indexed = repo.get(doc.path)
        return PaperUploadResponse(
            id=indexed.id,
            path=indexed.path,
            filename=indexed.filename,
        )
    finally:
        repo.close()


@router.post("/reference", response_model=PaperUploadResponse)
async def add_reference(
    body: AddReferenceRequest,
    config = Depends(get_config),
) -> PaperUploadResponse:
    """Register a reference entry (metadata-only, no associated file).

    Creates a paper-type document with ``source_type='reference'`` containing
    only the supplied metadata. BibTeX is auto-generated if not provided.
    """
    meta: dict[str, Any] = dict(body.extra_metadata or {})
    if body.title:
        meta["title"] = body.title
    if body.author:
        meta["author"] = body.author
    if body.year:
        meta["year"] = body.year
    if body.journal:
        meta["journal"] = body.journal
    if body.booktitle:
        meta["booktitle"] = body.booktitle
    if body.doi:
        meta["doi"] = body.doi
    if body.url:
        meta["url"] = body.url
    if body.bibtex:
        meta["bibtex"] = body.bibtex
    if body.citation_key:
        meta["citation_key"] = body.citation_key

    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        doc = indexer.add_file(
            "",  # filepath ignored for references
            document_type="reference",
            extra_metadata=meta or None,
        )
        if not doc:
            raise HTTPException(status_code=500, detail="Failed to create reference.")
        indexed = repo.get(doc.path)
        return PaperUploadResponse(
            id=indexed.id,
            path=indexed.path,
            filename=indexed.filename,
        )
    finally:
        repo.close()
