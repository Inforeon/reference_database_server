-- Migration: 001_initial
-- Creates the core schema for docsearch

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

-- Triggers to keep FTS synced on INSERT
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, filename, directory, full_text)
        VALUES (new.id, new.filename, new.directory, new.full_text);
END;

-- Triggers to keep FTS synced on DELETE
CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, filename, directory, full_text)
        VALUES ('delete', old.id, old.filename, old.directory, old.full_text);
END;

-- Triggers to keep FTS synced on UPDATE
CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, filename, directory, full_text)
        VALUES ('delete', old.id, old.filename, old.directory, old.full_text);
    INSERT INTO documents_fts(rowid, filename, directory, full_text)
        VALUES (new.id, new.filename, new.directory, new.full_text);
END;

CREATE INDEX IF NOT EXISTS idx_documents_directory ON documents(directory);
CREATE INDEX IF NOT EXISTS idx_documents_extension ON documents(extension);
