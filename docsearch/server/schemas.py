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


class ScanRequest(BaseModel):
    dirpath: str
    recursive: bool = True


class AddFileRequest(BaseModel):
    filepath: str


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
    """Response after uploading and indexing a file."""
    id: int
    path: str
    filename: str


class MetaPatch(BaseModel):
    """Partial update for sidecar metadata."""

    key: str
    value: Any
