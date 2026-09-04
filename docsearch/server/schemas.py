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
    source_type: str | None = None
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


# ── Paper-specific requests ──────────────────────────────────────

class AddPaperReferenceRequest(BaseModel):
    """Request to register a paper reference (metadata-only, no file)."""
    title: str
    filepath: str = ""  # Real path for grouping; file need not exist yet
    author: str | None = None
    year: str | None = None
    journal: str | None = None
    booktitle: str | None = None
    doi: str | None = None
    url: str | None = None
    bibtex: str | None = None
    citation_key: str | None = None
    extra_metadata: dict[str, Any] = {}


# ── Generic reference requests ───────────────────────────────────

class AddGenericReferenceRequest(BaseModel):
    """Request to register a generic document reference (metadata-only, no file)."""
    title: str
    filepath: str = ""  # Real path for grouping; file need not exist yet
    author: str | None = None
    subject: str | None = None       # subject/description
    keywords: list[str] | None = None
    url: str | None = None
    extra_metadata: dict[str, Any] = {}


# ── Textbook-specific requests ───────────────────────────────────

class SetChaptersRequest(BaseModel):
    """Request to redefine chapter breakpoints for a file-type textbook."""
    breakpoints: str  # JSON string: list [5,10] or dict {"Intro": 5, "Methods": null}


class AddTextbookRequest(BaseModel):
    """Request to add a textbook to the index."""
    filepath: str
    extra_metadata: dict[str, Any] = {}


class AddTextbookReferenceRequest(BaseModel):
    """Request to register a textbook reference (metadata-only, no file)."""
    title: str
    filepath: str = ""  # Real path for grouping; file need not exist yet
    author: str | None = None
    year: str | None = None
    publisher: str | None = None
    edition: str | None = None
    url: str | None = None
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


# ── Metadata slimming helpers ────────────────────────────────────────

def _format_author_slim(metadata: dict[str, Any]) -> str | None:
    """Extract a slim author string from metadata.

    Returns first author + "et al." if >3 authors, or full string if short.
    Handles plain strings, lists, and pdf2bib authors_bib dicts.
    """
    for key in ("author", "authors", "authors_bib"):
        value = metadata.get(key)
        if not value:
            continue
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, list):
            names = [
                _author_dict_name(v) if isinstance(v, dict) else str(v).strip()
                for v in value
                if v and str(v).strip()
            ]
            if not names:
                continue
            if len(names) > 3:
                return f"{names[0]} et al."
            return ", ".join(names[:3]) or None
    return None


def _author_dict_name(d: dict) -> str:
    """Format a single pdf2bib author dict as 'Given Family'."""
    given = d.get("given", "")
    family = d.get("family", "")
    return f"{given} {family}".strip() or str(d)


def _extract_year(metadata: dict[str, Any]) -> int | str | None:
    """Extract year from metadata, normalizing to int if possible."""
    year = metadata.get("year")
    if year is None:
        return None
    if isinstance(year, (int, float)):
        return int(year)
    try:
        return int(str(year))
    except (ValueError, TypeError):
        return str(year)


def _extract_title(metadata: dict[str, Any]) -> str | None:
    """Extract title from metadata."""
    title = metadata.get("title", "")
    return title.strip() if title else None


# ── Compact response schemas (default, less verbose) ───────────────

class CompactDocumentHit(BaseModel):
    """Slim document hit for search results (compact mode)."""
    id: int
    path: str
    document_type: str = "generic"
    title: str | None = None
    author: str | None = None
    year: int | str | None = None
    score: float = 0.0


class CompactChapterHit(BaseModel):
    """Slim chapter hit for search results (compact mode)."""
    chapter_id: int
    textbook_id: int
    chapter_index: int
    title: str
    pages: str  # "112–120" format
    score: float = 0.0


class CompactDocumentSearchGroup(BaseModel):
    """Paginated group of compact document-level search results."""
    results: list[CompactDocumentHit]
    total: int


class CompactChapterSearchGroup(BaseModel):
    """Paginated group of compact chapter-level search results."""
    results: list[CompactChapterHit]
    total: int


class CompactSearchResponse(BaseModel):
    """Combined compact search response with separated result groups."""
    documents: CompactDocumentSearchGroup
    chapters: CompactChapterSearchGroup


class CompactDocumentResponse(BaseModel):
    """Slim document detail (compact mode)."""
    id: int
    path: str
    document_type: str = "generic"
    source_type: str | None = None
    title: str | None = None
    author: str | None = None
    year: int | str | None = None
    chapter_count: int | None = None  # Only for textbooks


def _format_pages(start: int | None, end: int | None) -> str:
    """Format page range as 'start–end' string."""
    if start is not None and end is not None:
        return f"{start}–{end}"
    if start is not None:
        return f"{start}+"
    if end is not None:
        return f"–{end}"
    return ""

class SectionInfo(BaseModel):
    """Metadata for a single document section."""
    index: int
    name: str
    start: int
    end: int | None = None  # None means "to end of document"
    line_count: int


class SectionContentResponse(BaseModel):
    """A document section with its extracted text."""
    id: int
    path: str
    section_index: int
    section_name: str
    start: int
    end: int | None = None
    content: str


class SectionListResponse(BaseModel):
    """List of sections for a document."""
    id: int
    path: str
    sections: list[SectionInfo]


class SetSectionRequest(BaseModel):
    """Request to add a new section to a document."""
    name: str
    start: int
    end: int | None = None  # None means "to end of document"


class ChapterResponse(BaseModel):
    """Metadata for a single textbook chapter (no full_text)."""
    id: int
    textbook_id: int
    chapter_index: int
    title: str
    chapter_type: str = "range"
    start_page: int | None = None
    end_page: int | None = None
    page_count: int | None = None
    file_path: str | None = None
    metadata: dict[str, Any] = {}


class ChapterContentResponse(BaseModel):
    """A textbook chapter with its extracted text."""
    id: int
    textbook_id: int
    chapter_index: int
    title: str
    chapter_type: str = "range"
    start_page: int | None = None
    end_page: int | None = None
    page_count: int | None = None
    file_path: str | None = None
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


# ── Supplement schemas ─────────────────────────────────────────────

class SupplementResponse(BaseModel):
    """Metadata for a single paper supplement (no full_text)."""
    id: int
    paper_id: int
    supplement_index: int
    title: str
    file_path: str | None = None
    metadata: dict[str, Any] = {}


class SupplementContentResponse(BaseModel):
    """A paper supplement with its extracted text."""
    id: int
    paper_id: int
    supplement_index: int
    title: str
    file_path: str | None = None
    metadata: dict[str, Any] = {}
    content: str


class SupplementListResponse(BaseModel):
    """List of supplements for a paper."""
    id: int
    path: str
    supplements: list[SupplementResponse]


class SupplementSearchResultResponse(BaseModel):
    """A supplement-level search hit with parent paper context."""
    supplement: SupplementResponse
    parent_document: DocumentResponse
    score: float = 0.0


class SupplementSearchGroup(BaseModel):
    """Paginated group of supplement-level search results."""
    results: list[SupplementSearchResultResponse]
    total: int


class FullSearchResponse(BaseModel):
    """Combined search response with document, chapter, and supplement groups."""
    documents: DocumentSearchGroup
    chapters: ChapterSearchGroup
    supplements: SupplementSearchGroup


# ── Filesystem browsing schemas ────────────────────────────────────

class FileSystemEntry(BaseModel):
    """A single entry in a directory listing."""
    name: str
    type: str  # "file" or "directory"
    document_id: int | None = None


class DirectoryListingResponse(BaseModel):
    """Response for a directory listing request."""
    path: str
    entries: list[FileSystemEntry]
    directories: list[FileSystemEntry]
