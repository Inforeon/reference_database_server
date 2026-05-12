-- Add textbook_chapters table for chapter-level storage and search.
-- Each textbook (in the documents table) can have many chapters.
-- Chapter text and titles are indexed via FTS5 for full-text search.

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
