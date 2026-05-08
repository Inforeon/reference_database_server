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
            size=row[5] or 0,
            mtime=row[6] or 0.0,
            content_hash=row[7] or "",
            extracted_metadata=json.loads(row[8]) if row[8] else {},
            sidecar_metadata=json.loads(row[9]) if row[9] else {},
            full_text=row[10] or "",
            indexed_at=datetime.fromisoformat(row[11]) if row[11] else None,
        )


@dataclass
class SearchResult:
    """A single result from a search query."""

    document: Document
    score: float = 0.0
    snippet: str = ""


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
    offset: int = 0
    limit: int = 50
