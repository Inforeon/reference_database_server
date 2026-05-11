from __future__ import annotations

"""Pydantic schemas for the REST API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: int
    path: str
    filename: str
    directory: str
    extension: str
    document_type: str = "generic"
    size: int
    mtime: float
    metadata: dict[str, Any]
    indexed_at: datetime | None = None


class SearchResultResponse(BaseModel):
    document: DocumentResponse
    score: float = 0.0
    snippet: str = ""


class SearchRequest(BaseModel):
    q: str = ""
    scope: str = ""
    file_type: str = ""
    author: str = ""
    tags: list[str] = []
    after: str = ""
    before: str = ""
    offset: int = 0
    limit: int = 50


# ── Generic index requests (legacy, kept for backward compat) ────

class ScanRequest(BaseModel):
    dirpath: str
    recursive: bool = True
    document_type: str = "generic"
    extra_metadata: dict[str, Any] = {}


class AddFileRequest(BaseModel):
    filepath: str
    document_type: str = "generic"
    extra_metadata: dict[str, Any] = {}


# ── Paper-specific requests ──────────────────────────────────────

class AddPaperRequest(BaseModel):
    """Request to add a research paper to the index."""
    filepath: str
    doi: str | None = None
    skip_bib: bool = False
    extra_metadata: dict[str, Any] = {}


class UploadPaperQuery(BaseModel):
    """Query params for paper upload (for documentation)."""
    directory: str = ""
    filename: str | None = None
    doi: str | None = None
    skip_bib: bool = False
    extra_metadata: str | None = None


# ── Textbook-specific requests ───────────────────────────────────

class AddTextbookRequest(BaseModel):
    """Request to add a textbook to the index."""
    filepath: str
    extra_metadata: dict[str, Any] = {}


# ── Generic responses ────────────────────────────────────────────

class RemoveFileRequest(BaseModel):
    filepath: str


class IndexStats(BaseModel):
    added: int
    updated: int
    removed: int
    skipped: int
    errors: int


class ContentResponse(BaseModel):
    """Extracted text content of a document."""
    id: int
    path: str
    filename: str
    content: str


class UploadResponse(BaseModel):
    """Generic response after uploading and indexing a file."""
    id: int
    path: str
    filename: str


# Type aliases for clarity at call sites
PaperUploadResponse = UploadResponse
TextbookUploadResponse = UploadResponse


class MetaPatch(BaseModel):
    """Partial update for sidecar metadata."""

    key: str
    value: Any
