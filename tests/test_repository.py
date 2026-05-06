from __future__ import annotations

"""Tests for the docsearch project."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from docsearch.core.models import Document, SearchQuery, SearchResult
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
