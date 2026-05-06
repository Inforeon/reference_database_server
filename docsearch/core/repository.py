from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import Document, SearchQuery, SearchResult

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    directory TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER DEFAULT 0,
    mtime REAL DEFAULT 0,
    content_hash TEXT DEFAULT '',
    extracted_metadata TEXT DEFAULT '{}',
    sidecar_metadata TEXT DEFAULT '{}',
    full_text TEXT DEFAULT '',
    indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    filename, directory, full_text,
    content='documents',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, filename, directory, full_text)
        VALUES (new.id, new.filename, new.directory, new.full_text);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, filename, directory, full_text)
        VALUES ('delete', old.id, old.filename, old.directory, old.full_text);
END;

CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, filename, directory, full_text)
        VALUES ('delete', old.id, old.filename, old.directory, old.full_text);
    INSERT INTO documents_fts(rowid, filename, directory, full_text)
        VALUES (new.id, new.filename, new.directory, new.full_text);
END;

CREATE INDEX IF NOT EXISTS idx_documents_directory ON documents(directory);
CREATE INDEX IF NOT EXISTS idx_documents_extension ON documents(extension);
"""


class Repository:
    """SQLite-backed repository for storing and searching indexed documents."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create all tables, triggers, and indexes if they do not exist."""
        self._conn.executescript(_SCHEMA_SQL)

    @contextmanager
    def transaction(self):
        """Provide a cursor wrapped in an auto-commit/rollback transaction."""
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def upsert(self, doc: Document) -> int:
        """Insert or update a document. Returns the row id."""
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO documents (
                    path, filename, directory, extension, size, mtime,
                    content_hash, extracted_metadata, sidecar_metadata,
                    full_text, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    filename = excluded.filename,
                    directory = excluded.directory,
                    extension = excluded.extension,
                    size = excluded.size,
                    mtime = excluded.mtime,
                    content_hash = excluded.content_hash,
                    extracted_metadata = excluded.extracted_metadata,
                    sidecar_metadata = excluded.sidecar_metadata,
                    full_text = excluded.full_text,
                    indexed_at = excluded.indexed_at
                """,
                (
                    doc.path,
                    doc.filename,
                    doc.directory,
                    doc.extension,
                    doc.size,
                    doc.mtime,
                    doc.content_hash,
                    json.dumps(doc.extracted_metadata),
                    json.dumps(doc.sidecar_metadata),
                    doc.full_text,
                    doc.indexed_at.isoformat() if doc.indexed_at else None,
                ),
            )
            return cur.lastrowid

    def remove(self, path: str) -> bool:
        """Remove a document by path. Returns True if found."""
        with self.transaction() as cur:
            cur.execute("DELETE FROM documents WHERE path = ?", (path,))
            return cur.rowcount > 0

    def get(self, path: str) -> Optional[Document]:
        """Get a document by path."""
        cur = self._conn.execute(
            "SELECT * FROM documents WHERE path = ?", (path,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return Document.from_row(row)

    def get_by_id(self, doc_id: int) -> Optional[Document]:
        """Get a document by internal ID."""
        cur = self._conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return Document.from_row(row)

    def exists(self, path: str) -> bool:
        """Check if a path is already indexed."""
        cur = self._conn.execute(
            "SELECT 1 FROM documents WHERE path = ?", (path,)
        )
        return cur.fetchone() is not None

    def all_paths(self) -> list[str]:
        """Return all indexed paths."""
        cur = self._conn.execute("SELECT path FROM documents ORDER BY path")
        return [row["path"] for row in cur.fetchall()]

    def count(self) -> int:
        """Return total number of indexed documents."""
        cur = self._conn.execute("SELECT COUNT(*) AS c FROM documents")
        return cur.fetchone()["c"]

    def search(self, query: SearchQuery) -> list[SearchResult]:
        """Execute a search query, returning ranked results.

        Combines FTS5 full-text matching with WHERE clause filters.
        Builds dynamic SQL based on which filters are provided.
        """
        conditions: list[str] = []
        params: list = []
        use_fts = bool(query.q)

        # FTS full-text match — must use bare table name, not alias
        if use_fts:
            conditions.append("documents_fts MATCH ?")
            params.append(query.q)

        # Directory scope filter
        if query.scope:
            conditions.append("d.directory LIKE ?")
            params.append(f"{query.scope}/%")

        # File type / extension filter
        if query.file_type:
            conditions.append("d.extension = ?")
            params.append(query.file_type)

        # Author metadata filter
        if query.author:
            conditions.append("json_extract(d.extracted_metadata, '$.author') = ?")
            params.append(query.author)

        # Tags filter — check JSON array contains each tag
        for tag in query.tags:
            conditions.append("json_extract(d.sidecar_metadata, '$.tags') LIKE ?")
            params.append(f"%\"{tag}\"%")

        # Date range filters on mtime
        if query.after:
            ts = datetime.fromisoformat(query.after).timestamp()
            conditions.append("d.mtime >= ?")
            params.append(ts)

        if query.before:
            ts = datetime.fromisoformat(query.before).timestamp()
            conditions.append("d.mtime <= ?")
            params.append(ts)

        # Build the final query
        where_clause = " AND ".join(conditions) if conditions else "1=1"

        if use_fts:
            sql = f"""
                SELECT d.*, rank AS relevance
                FROM documents d
                JOIN documents_fts ON d.id = documents_fts.rowid
                WHERE {where_clause}
                ORDER BY relevance DESC, d.filename ASC
                LIMIT ? OFFSET ?
            """
        else:
            sql = f"""
                SELECT d.*, 0.0 AS relevance
                FROM documents d
                WHERE {where_clause}
                ORDER BY d.filename ASC
                LIMIT ? OFFSET ?
            """

        params.extend([query.limit, query.offset])

        cur = self._conn.execute(sql, params)
        results: list[SearchResult] = []
        for row in cur.fetchall():
            doc = Document.from_row(row)
            results.append(
                SearchResult(
                    document=doc,
                    score=row["relevance"] if row["relevance"] else 0.0,
                )
            )
        return results

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()
