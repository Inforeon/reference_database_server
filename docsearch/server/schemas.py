from __future__ import annotations

"""Pydantic schemas for the REST API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class DocumentResponse(BaseModel):
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


class MetaPatch(BaseModel):
    """Partial update for sidecar metadata."""

    key: str
    value: Any
