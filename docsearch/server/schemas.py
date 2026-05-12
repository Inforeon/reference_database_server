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


# ── Textbook-specific requests ───────────────────────────────────

class AddTextbookRequest(BaseModel):
    """Request to add a textbook to the index."""
    filepath: str
    extra_metadata: dict[str, Any] = {}


# ── Generic responses ────────────────────────────────────────────

class RemoveFileRequest(BaseModel):
    filepath: str


class MoveDocumentRequest(BaseModel):
    """Request to move a document to a new location within the database home."""
    destination: str


class MoveDocumentResponse(BaseModel):
    """Response after successfully moving a document."""
    id: int
    old_path: str
    new_path: str
    filename: str


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


# ── Chapter schemas ────────────────────────────────────────────────

class ChapterResponse(BaseModel):
    """Metadata for a single textbook chapter (no full_text)."""
    id: int
    textbook_id: int
    chapter_index: int
    title: str
    start_page: int
    end_page: int
    metadata: dict[str, Any] = {}


class ChapterContentResponse(BaseModel):
    """A textbook chapter with its extracted text."""
    id: int
    textbook_id: int
    chapter_index: int
    title: str
    start_page: int
    end_page: int
    metadata: dict[str, Any] = {}
    full_text: str


class ChapterSearchResultResponse(BaseModel):
    """A chapter-level search hit with parent textbook context."""
    chapter: ChapterResponse
    parent_document: DocumentResponse
    score: float = 0.0


class DocumentSearchGroup(BaseModel):
    """Paginated group of document-level search results."""
    results: list[SearchResultResponse]
    total: int


class ChapterSearchGroup(BaseModel):
    """Paginated group of chapter-level search results."""
    results: list[ChapterSearchResultResponse]
    total: int


class SearchResponse(BaseModel):
    """Combined search response with separated result groups."""
    documents: DocumentSearchGroup
    chapters: ChapterSearchGroup
