from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .models import Chapter, Document, SearchQuery, SearchResult

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    directory TEXT NOT NULL,
    extension TEXT NOT NULL,
    document_type TEXT DEFAULT 'generic',
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

CREATE TABLE IF NOT EXISTS textbook_chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    textbook_id INTEGER NOT NULL,
    chapter_index INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    start_page INTEGER DEFAULT 1,
    end_page INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    full_text TEXT DEFAULT '',
    FOREIGN KEY (textbook_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS textbook_chapters_fts USING fts5(
    title, full_text,
    content='textbook_chapters',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS chapters_ai AFTER INSERT ON textbook_chapters BEGIN
    INSERT INTO textbook_chapters_fts(rowid, title, full_text)
        VALUES (new.id, new.title, new.full_text);
END;

CREATE TRIGGER IF NOT EXISTS chapters_ad AFTER DELETE ON textbook_chapters BEGIN
    INSERT INTO textbook_chapters_fts(textbook_chapters_fts, rowid, title, full_text)
        VALUES ('delete', old.id, old.title, old.full_text);
END;

CREATE TRIGGER IF NOT EXISTS chapters_au AFTER UPDATE ON textbook_chapters BEGIN
    INSERT INTO textbook_chapters_fts(textbook_chapters_fts, rowid, title, full_text)
        VALUES ('delete', old.id, old.title, old.full_text);
    INSERT INTO textbook_chapters_fts(rowid, title, full_text)
        VALUES (new.id, new.title, new.full_text);
END;

CREATE INDEX IF NOT EXISTS idx_tc_textbook ON textbook_chapters(textbook_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tc_chapter ON textbook_chapters(textbook_id, chapter_index);
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
                    path, filename, directory, extension, document_type, size, mtime,
                    content_hash, extracted_metadata, sidecar_metadata,
                    full_text, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    filename = excluded.filename,
                    directory = excluded.directory,
                    extension = excluded.extension,
                    document_type = excluded.document_type,
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
                    doc.document_type,
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

    def rename(self, old_path: str, new_path: str) -> bool:
        """Update the path, filename, and directory of an existing document.

        Preserves the internal ``id`` so that foreign-key references
        (e.g. textbook chapters) remain valid.  Returns True when a row
        was updated.
        """
        new_p = Path(new_path)
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE documents
                SET path = ?, filename = ?, directory = ?
                WHERE path = ?
                """,
                (str(new_p), new_p.name, str(new_p.parent), old_path),
            )
            return cur.rowcount > 0

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

        # Document type filter
        if query.document_types:
            placeholders = ",".join("?" * len(query.document_types))
            conditions.append(f"d.document_type IN ({placeholders})")
            params.extend(query.document_types)

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

    # ── Chapter methods ────────────────────────────────────────────────

    def upsert_chapter(self, chapter: Chapter) -> int:
        """Insert or update a chapter. Returns the row id."""
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO textbook_chapters (
                    textbook_id, chapter_index, title, start_page, end_page,
                    metadata, full_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(textbook_id, chapter_index) DO UPDATE SET
                    title = excluded.title,
                    start_page = excluded.start_page,
                    end_page = excluded.end_page,
                    metadata = excluded.metadata,
                    full_text = excluded.full_text
                """,
                (
                    chapter.textbook_id,
                    chapter.chapter_index,
                    chapter.title,
                    chapter.start_page,
                    chapter.end_page,
                    json.dumps(chapter.metadata),
                    chapter.full_text,
                ),
            )
            return cur.lastrowid

    def get_chapters(self, textbook_id: int) -> list[Chapter]:
        """Return all chapters for a textbook, ordered by chapter_index."""
        cur = self._conn.execute(
            "SELECT * FROM textbook_chapters WHERE textbook_id = ? ORDER BY chapter_index",
            (textbook_id,),
        )
        return [Chapter.from_row(row) for row in cur.fetchall()]

    def get_chapter(self, textbook_id: int, chapter_index: int) -> Optional[Chapter]:
        """Return a specific chapter by index."""
        cur = self._conn.execute(
            "SELECT * FROM textbook_chapters WHERE textbook_id = ? AND chapter_index = ?",
            (textbook_id, chapter_index),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return Chapter.from_row(row)

    def delete_chapters(self, textbook_id: int) -> int:
        """Delete all chapters for a textbook. Returns number of rows deleted."""
        with self.transaction() as cur:
            cur.execute(
                "DELETE FROM textbook_chapters WHERE textbook_id = ?",
                (textbook_id,),
            )
            return cur.rowcount

    def search_textbook_chapters(self, query: SearchQuery) -> list[SearchResult]:
        """Search textbook chapters using a two-phase approach.

        Phase 1: Resolve which textbooks match metadata filters.
        Phase 2: Search FTS within those textbooks' chapters.
        """
        # Phase 1 — find matching textbook ids from metadata filters
        textbook_ids = self._resolve_textbook_ids(query)
        if not textbook_ids:
            return []

        placeholders = ",".join("?" * len(textbook_ids))

        # Phase 2 — FTS search within matched textbooks' chapters
        conditions: list[str] = [f"tc.textbook_id IN ({placeholders})"]
        params: list[Any] = list(textbook_ids)
        use_fts = bool(query.q)

        if use_fts:
            conditions.append("textbook_chapters_fts MATCH ?")
            params.append(query.q)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        if use_fts:
            sql = f"""
                SELECT tc.id AS ch_id, tc.textbook_id, tc.chapter_index,
                       tc.title AS ch_title, tc.start_page, tc.end_page,
                       tc.metadata AS ch_metadata, tc.full_text AS ch_full_text,
                       d.id AS doc_id, d.path, d.filename, d.directory, d.extension,
                       d.document_type, d.size, d.mtime, d.content_hash,
                       d.extracted_metadata, d.sidecar_metadata,
                       d.full_text AS doc_full_text, d.indexed_at,
                       rank AS relevance
                FROM textbook_chapters tc
                JOIN textbook_chapters_fts ON tc.id = textbook_chapters_fts.rowid
                JOIN documents d ON d.id = tc.textbook_id
                WHERE {where_clause}
                ORDER BY relevance DESC, tc.chapter_index ASC
                LIMIT ? OFFSET ?
            """
        else:
            sql = f"""
                SELECT tc.id AS ch_id, tc.textbook_id, tc.chapter_index,
                       tc.title AS ch_title, tc.start_page, tc.end_page,
                       tc.metadata AS ch_metadata, tc.full_text AS ch_full_text,
                       d.id AS doc_id, d.path, d.filename, d.directory, d.extension,
                       d.document_type, d.size, d.mtime, d.content_hash,
                       d.extracted_metadata, d.sidecar_metadata,
                       d.full_text AS doc_full_text, d.indexed_at,
                       0.0 AS relevance
                FROM textbook_chapters tc
                JOIN documents d ON d.id = tc.textbook_id
                WHERE {where_clause}
                ORDER BY tc.chapter_index ASC
                LIMIT ? OFFSET ?
            """

        params.extend([query.limit, query.offset])

        cur = self._conn.execute(sql, params)
        results: list[SearchResult] = []
        for row in cur.fetchall():
            # Build chapter from prefixed columns
            chapter_row = {
                "id": row["ch_id"],
                "textbook_id": row["textbook_id"],
                "chapter_index": row["chapter_index"],
                "title": row["ch_title"],
                "start_page": row["start_page"],
                "end_page": row["end_page"],
                "metadata": row["ch_metadata"],
                "full_text": row["ch_full_text"],
            }
            chapter = Chapter.from_row(chapter_row)
            # Build document from prefixed columns
            doc_row = {
                "id": row["doc_id"],
                "path": row["path"],
                "filename": row["filename"],
                "directory": row["directory"],
                "extension": row["extension"],
                "document_type": row["document_type"],
                "size": row["size"],
                "mtime": row["mtime"],
                "content_hash": row["content_hash"],
                "extracted_metadata": row["extracted_metadata"],
                "sidecar_metadata": row["sidecar_metadata"],
                "full_text": row["doc_full_text"],
                "indexed_at": row["indexed_at"],
            }
            doc = Document.from_row(doc_row)
            results.append(
                SearchResult(
                    document=doc,
                    chapter=chapter,
                    score=row["relevance"] if row["relevance"] else 0.0,
                )
            )
        return results

    def _resolve_textbook_ids(self, query: SearchQuery) -> list[int]:
        """Find textbook document ids that match the given metadata filters."""
        conditions: list[str] = ["document_type = 'textbook'"]
        params: list[Any] = []

        if query.scope:
            conditions.append("directory LIKE ?")
            params.append(f"{query.scope}/%")

        if query.file_type:
            conditions.append("extension = ?")
            params.append(query.file_type)

        if query.author:
            conditions.append("json_extract(extracted_metadata, '$.author') = ?")
            params.append(query.author)

        for tag in query.tags:
            conditions.append("json_extract(sidecar_metadata, '$.tags') LIKE ?")
            params.append(f"%\"{tag}\"%")

        if query.after:
            ts = datetime.fromisoformat(query.after).timestamp()
            conditions.append("mtime >= ?")
            params.append(ts)

        if query.before:
            ts = datetime.fromisoformat(query.before).timestamp()
            conditions.append("mtime <= ?")
            params.append(ts)

        where_clause = " AND ".join(conditions)
        cur = self._conn.execute(
            f"SELECT id FROM documents WHERE {where_clause}", params
        )
        return [row["id"] for row in cur.fetchall()]

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()
