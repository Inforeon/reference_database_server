from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class Document:
    """A single indexed document."""

    id: Optional[int] = None
    path: str = ""
    filename: str = ""
    directory: str = ""
    extension: str = ""
    document_type: str = "generic"
    size: int = 0
    mtime: float = 0.0
    content_hash: str = ""
    extracted_metadata: dict[str, Any] = field(default_factory=dict)
    sidecar_metadata: dict[str, Any] = field(default_factory=dict)
    full_text: str = ""
    indexed_at: Optional[datetime] = None

    @property
    def combined_metadata(self) -> dict[str, Any]:
        """Return merged metadata: extracted first, then sidecar overrides."""
        merged = dict(self.extracted_metadata)
        merged.update(self.sidecar_metadata)
        return merged

    @classmethod
    def from_row(cls, row: tuple | dict | sqlite3.Row) -> "Document":
        """Create from a database row (tuple, sqlite3.Row, or dict)."""
        # Support dict-like rows (sqlite3.Row is a mapping but not a dict)
        if hasattr(row, "keys"):
            keys = row.keys()
            extracted_raw = row["extracted_metadata"] if "extracted_metadata" in keys else None
            sidecar_raw = row["sidecar_metadata"] if "sidecar_metadata" in keys else None
            indexed_raw = row["indexed_at"] if "indexed_at" in keys else None
            return cls(
                id=row["id"] if "id" in keys else None,
                path=row["path"],
                filename=row["filename"],
                directory=row["directory"],
                extension=row["extension"],
                document_type=row["document_type"] if "document_type" in keys and row["document_type"] else "generic",
                size=row["size"] if "size" in keys and row["size"] else 0,
                mtime=row["mtime"] if "mtime" in keys and row["mtime"] else 0.0,
                content_hash=row["content_hash"] if "content_hash" in keys and row["content_hash"] else "",
                extracted_metadata=json.loads(extracted_raw) if extracted_raw else {},
                sidecar_metadata=json.loads(sidecar_raw) if sidecar_raw else {},
                full_text=row["full_text"] if "full_text" in keys and row["full_text"] else "",
                indexed_at=datetime.fromisoformat(indexed_raw) if indexed_raw else None,
            )

        # Fallback: positional tuple (includes id as first element)
        return cls(
            id=row[0],
            path=row[1],
            filename=row[2],
            directory=row[3],
            extension=row[4],
            document_type=row[5] if len(row) > 5 and row[5] else "generic",
            size=row[6] if len(row) > 6 and row[6] else 0,
            mtime=row[7] if len(row) > 7 and row[7] else 0.0,
            content_hash=row[8] if len(row) > 8 and row[8] else "",
            extracted_metadata=json.loads(row[9]) if len(row) > 9 and row[9] else {},
            sidecar_metadata=json.loads(row[10]) if len(row) > 10 and row[10] else {},
            full_text=row[11] if len(row) > 11 and row[11] else "",
            indexed_at=datetime.fromisoformat(row[12]) if len(row) > 12 and row[12] else None,
        )


@dataclass
class Chapter:
    """A chapter within a textbook."""

    id: Optional[int] = None
    textbook_id: int = 0
    chapter_index: int = 0
    title: str = ""
    start_page: int = 1
    end_page: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    full_text: str = ""

    def combined_metadata(self, parent_document: Optional[Document] = None) -> dict[str, Any]:
        """Return merged metadata: parent textbook first, then chapter overrides."""
        merged: dict[str, Any] = {}
        if parent_document:
            merged.update(parent_document.extracted_metadata)
            merged.update(parent_document.sidecar_metadata)
        merged.update(self.metadata)
        return merged

    @classmethod
    def from_row(cls, row: tuple | dict | sqlite3.Row) -> "Chapter":
        """Create from a database row (tuple, sqlite3.Row, or dict)."""
        if hasattr(row, "keys"):
            keys = row.keys()
            meta_raw = row["metadata"] if "metadata" in keys else None
            return cls(
                id=row["id"] if "id" in keys else None,
                textbook_id=row["textbook_id"] if "textbook_id" in keys else 0,
                chapter_index=row["chapter_index"] if "chapter_index" in keys else 0,
                title=row["title"] if "title" in keys and row["title"] else "",
                start_page=row["start_page"] if "start_page" in keys and row["start_page"] else 1,
                end_page=row["end_page"] if "end_page" in keys and row["end_page"] else 0,
                metadata=json.loads(meta_raw) if meta_raw else {},
                full_text=row["full_text"] if "full_text" in keys and row["full_text"] else "",
            )

        # Fallback: positional tuple
        return cls(
            id=row[0] if len(row) > 0 else None,
            textbook_id=row[1] if len(row) > 1 else 0,
            chapter_index=row[2] if len(row) > 2 else 0,
            title=row[3] if len(row) > 3 and row[3] else "",
            start_page=row[4] if len(row) > 4 and row[4] else 1,
            end_page=row[5] if len(row) > 5 and row[5] else 0,
            metadata=json.loads(row[6]) if len(row) > 6 and row[6] else {},
            full_text=row[7] if len(row) > 7 and row[7] else "",
        )


@dataclass
class SearchResult:
    """A single result from a search query."""

    document: Document
    score: float = 0.0
    snippet: str = ""
    chapter: Optional[Chapter] = None


@dataclass
class SearchQuery:
    """Parameters for a search query."""

    q: str = ""                  # Full-text search query
    scope: str = ""              # Restrict to subdirectory prefix
    file_type: str = ""          # Filter by extension (e.g., "pdf")
    author: str = ""             # Filter by author field
    tags: list[str] = field(default_factory=list)  # Filter by tags array
    after: str = ""              # ISO date string, filter mtime >=
    before: str = ""             # ISO date string, filter mtime <=
    document_types: list[str] = field(default_factory=list)  # Empty = all types
    offset: int = 0
    limit: int = 50

    def includes_type(self, doc_type: str) -> bool:
        """Check if this query should include results of the given document type."""
        if not self.document_types:
            return True
        return doc_type in self.document_types
