from __future__ import annotations

"""Tests for the docsearch project."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from docsearch.core.models import Chapter, Document, SearchQuery, SearchResult
from docsearch.core.repository import Repository


@pytest.fixture()
def db_path(tmp_path: Path):
    """Return a unique temp database path for each test."""
    return str(tmp_path / "test.db")


@pytest.fixture()
def repo(db_path: str):
    """Create a fresh repository for each test."""
    r = Repository(db_path)
    yield r
    r.close()


def _make_doc(path: str = "/tmp/test.md", **kwargs) -> Document:
    defaults = dict(
        path=path,
        filename=Path(path).name,
        directory=str(Path(path).parent),
        extension="md",
        size=100,
        mtime=1700000000.0,
        content_hash="abc123",
        extracted_metadata={"author": "Alice"},
        sidecar_metadata={"tags": ["test"]},
        full_text="hello world this is a test document",
    )
    defaults.update(kwargs)
    return Document(**defaults)


class TestDocument:
    def test_combined_metadata_merges(self):
        doc = Document(
            path="/x/a.md",
            filename="a.md",
            directory="/x",
            extension="md",
            extracted_metadata={"author": "Alice", "title": "Draft"},
            sidecar_metadata={"tags": ["final"], "title": "Final"},
        )
        combined = doc.combined_metadata
        assert combined["author"] == "Alice"
        assert combined["tags"] == ["final"]
        assert combined["title"] == "Final"  # sidecar overrides

    def test_from_row_tuple(self):
        row = (
            1,
            "/tmp/test.md",
            "test.md",
            "/tmp",
            "md",
            "generic",
            100,
            1700000000.0,
            "abc123",
            '{"author": "Bob"}',
            '{"tags": ["x"]}',
            "some text",
            None,
        )
        doc = Document.from_row(row)
        assert doc.id == 1
        assert doc.path == "/tmp/test.md"
        assert doc.extracted_metadata == {"author": "Bob"}
        assert doc.sidecar_metadata == {"tags": ["x"]}
        assert doc.indexed_at is None

    def test_from_row_dict(self):
        row = {
            "id": 2,
            "path": "/tmp/test.md",
            "filename": "test.md",
            "directory": "/tmp",
            "extension": "md",
            "size": 100,
            "mtime": 1700000000.0,
            "content_hash": "abc123",
            "extracted_metadata": '{"author": "Bob"}',
            "sidecar_metadata": '{"tags": ["x"]}',
            "full_text": "some text",
            "indexed_at": None,
        }
        doc = Document.from_row(row)
        assert doc.id == 2
        assert doc.path == "/tmp/test.md"
        assert doc.extracted_metadata == {"author": "Bob"}
        assert doc.sidecar_metadata == {"tags": ["x"]}
        assert doc.indexed_at is None


class TestRepository:
    def test_upsert_and_get(self, repo: Repository):
        doc = _make_doc()
        repo.upsert(doc)
        fetched = repo.get(doc.path)
        assert fetched is not None
        assert fetched.filename == doc.filename
        assert fetched.extracted_metadata == doc.extracted_metadata

    def test_upsert_updates_existing(self, repo: Repository):
        doc = _make_doc(full_text="first version")
        repo.upsert(doc)
        doc.full_text = "second version updated"
        repo.upsert(doc)
        fetched = repo.get(doc.path)
        assert fetched is not None
        assert "second version" in fetched.full_text

    def test_remove(self, repo: Repository):
        doc = _make_doc()
        repo.upsert(doc)
        assert repo.exists(doc.path)
        assert repo.remove(doc.path)
        assert not repo.exists(doc.path)

    def test_remove_missing_returns_false(self, repo: Repository):
        assert not repo.remove("/nonexistent/file.md")

    def test_count(self, repo: Repository):
        assert repo.count() == 0
        repo.upsert(_make_doc("/a/1.md"))
        repo.upsert(_make_doc("/a/2.md"))
        assert repo.count() == 2

    def test_all_paths(self, repo: Repository):
        repo.upsert(_make_doc("/a/1.md"))
        repo.upsert(_make_doc("/b/2.pdf"))
        paths = repo.all_paths()
        assert "/a/1.md" in paths
        assert "/b/2.pdf" in paths

    def test_search_fulltext(self, repo: Repository):
        repo.upsert(_make_doc("/a/one.md", full_text="the quick brown fox"))
        repo.upsert(_make_doc("/b/two.md", full_text="lazy dog sleeps"))
        results = repo.search(SearchQuery(q="quick"))
        assert len(results) == 1
        assert results[0].document.path == "/a/one.md"

    def test_search_scope_filter(self, repo: Repository):
        repo.upsert(_make_doc("/docs/a/1.md", full_text="secret info"))
        repo.upsert(_make_doc("/public/b/2.md", full_text="secret info"))
        results = repo.search(SearchQuery(q="secret", scope="/docs"))
        assert len(results) == 1
        assert "/docs/" in results[0].document.path

    def test_search_author_filter(self, repo: Repository):
        repo.upsert(_make_doc("/a/x.md", extracted_metadata={"author": "Alice"}, full_text="report"))
        repo.upsert(_make_doc("/a/y.md", extracted_metadata={"author": "Bob"}, full_text="report"))
        results = repo.search(SearchQuery(author="Alice"))
        assert len(results) == 1
        assert results[0].document.extracted_metadata["author"] == "Alice"

    def test_search_extension_filter(self, repo: Repository):
        repo.upsert(_make_doc("/a/file.md", extension="md", full_text="data"))
        repo.upsert(_make_doc("/a/file.pdf", extension="pdf", full_text="data"))
        results = repo.search(SearchQuery(file_type="pdf"))
        assert len(results) == 1
        assert results[0].document.extension == "pdf"

    def test_search_tags_filter(self, repo: Repository):
        repo.upsert(_make_doc("/a/x.md", sidecar_metadata={"tags": ["finance", "report"]}, full_text="numbers"))
        repo.upsert(_make_doc("/a/y.md", sidecar_metadata={"tags": ["personal"]}, full_text="diary"))
        results = repo.search(SearchQuery(tags=["finance"]))
        assert len(results) == 1
        assert "finance" in results[0].document.sidecar_metadata["tags"]

    def test_search_limit_offset(self, repo: Repository):
        for i in range(5):
            repo.upsert(_make_doc(f"/a/{i}.md", full_text=f"doc number {i}"))
        page1 = repo.search(SearchQuery(limit=2, offset=0))
        page2 = repo.search(SearchQuery(limit=2, offset=2))
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0].document.path != page2[0].document.path

    def test_get_by_id(self, repo: Repository):
        doc = _make_doc()
        repo.upsert(doc)
        fetched = repo.get_by_id(1)
        assert fetched is not None
        assert fetched.path == doc.path

    def test_empty_query_returns_all(self, repo: Repository):
        repo.upsert(_make_doc("/a/1.md", full_text="alpha"))
        repo.upsert(_make_doc("/a/2.md", full_text="beta"))
        results = repo.search(SearchQuery())
        assert len(results) == 2

    def test_search_document_type_filter(self, repo: Repository):
        repo.upsert(_make_doc("/a/1.md", document_type="generic", full_text="data"))
        repo.upsert(_make_doc("/a/2.md", document_type="paper", full_text="data"))
        results = repo.search(SearchQuery(document_types=["paper"]))
        assert len(results) == 1
        assert results[0].document.document_type == "paper"


class TestChapterModel:
    def test_combined_metadata_inherits_from_parent(self):
        parent = Document(
            path="/x/book.pdf",
            extracted_metadata={"author": "Klein", "title": "Book"},
            sidecar_metadata={"tags": ["physics"]},
        )
        chapter = Chapter(
            textbook_id=1,
            chapter_index=0,
            title="Ch 1",
            metadata={"subtitle": "Intro"},
        )
        combined = chapter.combined_metadata(parent)
        assert combined["author"] == "Klein"
        assert combined["title"] == "Book"
        assert combined["tags"] == ["physics"]
        assert combined["subtitle"] == "Intro"

    def test_combined_metadata_without_parent(self):
        chapter = Chapter(
            textbook_id=1,
            chapter_index=0,
            metadata={"key": "val"},
        )
        combined = chapter.combined_metadata()
        assert combined == {"key": "val"}

    def test_combined_metadata_chapter_overrides_parent(self):
        parent = Document(
            extracted_metadata={"title": "Parent Title"},
        )
        chapter = Chapter(
            textbook_id=1,
            chapter_index=0,
            metadata={"title": "Chapter Title"},
        )
        combined = chapter.combined_metadata(parent)
        assert combined["title"] == "Chapter Title"

    def test_from_row_dict(self):
        row = {
            "id": 1,
            "textbook_id": 5,
            "chapter_index": 2,
            "title": "Quantum Mechanics",
            "start_page": 100,
            "end_page": 150,
            "metadata": '{"topic": "quantum"}',
            "full_text": "some chapter text",
        }
        ch = Chapter.from_row(row)
        assert ch.id == 1
        assert ch.textbook_id == 5
        assert ch.chapter_index == 2
        assert ch.title == "Quantum Mechanics"
        assert ch.start_page == 100
        assert ch.end_page == 150
        assert ch.metadata == {"topic": "quantum"}
        assert ch.full_text == "some chapter text"


class TestChapterRepository:
    def _make_textbook(self, repo: Repository, **kwargs) -> Document:
        defaults = dict(
            path="/tmp/textbook.pdf",
            filename="textbook.pdf",
            directory="/tmp",
            extension="pdf",
            document_type="textbook",
            size=5000,
            mtime=1700000000.0,
            content_hash="xyz",
            extracted_metadata={"author": "Klein", "title": "Test Book"},
            sidecar_metadata={},
            full_text="TOC",
        )
        defaults.update(kwargs)
        doc = Document(**defaults)
        repo.upsert(doc)
        return repo.get(doc.path)

    def test_upsert_and_get_chapters(self, repo: Repository):
        tb = self._make_textbook(repo)
        repo.upsert_chapter(Chapter(textbook_id=tb.id, chapter_index=0, title="Ch 1", start_page=1, end_page=10, full_text="first chapter"))
        repo.upsert_chapter(Chapter(textbook_id=tb.id, chapter_index=1, title="Ch 2", start_page=11, end_page=20, full_text="second chapter"))

        chapters = repo.get_chapters(tb.id)
        assert len(chapters) == 2
        assert chapters[0].title == "Ch 1"
        assert chapters[1].title == "Ch 2"

    def test_get_chapter_by_index(self, repo: Repository):
        tb = self._make_textbook(repo)
        repo.upsert_chapter(Chapter(textbook_id=tb.id, chapter_index=0, title="Intro", full_text="intro text"))
        repo.upsert_chapter(Chapter(textbook_id=tb.id, chapter_index=1, title="Core", full_text="core text"))

        ch = repo.get_chapter(tb.id, 1)
        assert ch is not None
        assert ch.title == "Core"
        assert ch.full_text == "core text"

    def test_get_missing_chapter_returns_none(self, repo: Repository):
        tb = self._make_textbook(repo)
        assert repo.get_chapter(tb.id, 99) is None

    def test_delete_chapters(self, repo: Repository):
        tb = self._make_textbook(repo)
        repo.upsert_chapter(Chapter(textbook_id=tb.id, chapter_index=0, title="A", full_text="a"))
        repo.upsert_chapter(Chapter(textbook_id=tb.id, chapter_index=1, title="B", full_text="b"))
        assert len(repo.get_chapters(tb.id)) == 2

        deleted = repo.delete_chapters(tb.id)
        assert deleted == 2
        assert len(repo.get_chapters(tb.id)) == 0

    def test_upsert_chapter_updates_existing(self, repo: Repository):
        tb = self._make_textbook(repo)
        repo.upsert_chapter(Chapter(textbook_id=tb.id, chapter_index=0, title="Old", full_text="old text"))
        repo.upsert_chapter(Chapter(textbook_id=tb.id, chapter_index=0, title="New", full_text="new text"))

        chapters = repo.get_chapters(tb.id)
        assert len(chapters) == 1
        assert chapters[0].title == "New"

    def test_search_chapters_fts(self, repo: Repository):
        tb = self._make_textbook(repo)
        repo.upsert_chapter(Chapter(textbook_id=tb.id, chapter_index=0, title="Thermodynamics", full_text="entropy and free energy"))
        repo.upsert_chapter(Chapter(textbook_id=tb.id, chapter_index=1, title="Electromagnetism", full_text="magnetic fields and induction"))

        results = repo.search_textbook_chapters(SearchQuery(q="entropy"))
        assert len(results) == 1
        assert results[0].chapter.title == "Thermodynamics"
        assert results[0].document.extracted_metadata["author"] == "Klein"

    def test_search_chapters_by_title(self, repo: Repository):
        tb = self._make_textbook(repo)
        repo.upsert_chapter(Chapter(textbook_id=tb.id, chapter_index=0, title="Quantum Mechanics", full_text="wave functions"))
        results = repo.search_textbook_chapters(SearchQuery(q="Quantum"))
        assert len(results) == 1

    def test_search_chapters_resolves_textbook_ids(self, repo: Repository):
        # Two textbooks, only one matches author filter
        tb1 = self._make_textbook(repo, extracted_metadata={"author": "Klein"})
        tb2_data = Document(
            path="/tmp/other.pdf",
            filename="other.pdf",
            directory="/tmp",
            extension="pdf",
            document_type="textbook",
            extracted_metadata={"author": "Griffiths"},
        )
        repo.upsert(tb2_data)
        tb2 = repo.get(tb2_data.path)

        repo.upsert_chapter(Chapter(textbook_id=tb1.id, chapter_index=0, title="Ch1", full_text="physics content"))
        repo.upsert_chapter(Chapter(textbook_id=tb2.id, chapter_index=0, title="Ch1", full_text="physics content"))

        results = repo.search_textbook_chapters(SearchQuery(q="physics", author="Klein"))
        assert len(results) == 1
        assert results[0].document.extracted_metadata["author"] == "Klein"

    def test_search_chapters_no_match_returns_empty(self, repo: Repository):
        results = repo.search_textbook_chapters(SearchQuery(q="nothing"))
        assert len(results) == 0

    def test_cascade_delete_on_document_remove(self, repo: Repository):
        tb = self._make_textbook(repo)
        repo.upsert_chapter(Chapter(textbook_id=tb.id, chapter_index=0, title="A", full_text="a"))
        repo.remove(tb.path)
        assert len(repo.get_chapters(tb.id)) == 0
