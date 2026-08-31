from __future__ import annotations

"""Tests for control-character sanitization of extracted text.

PDF extraction can emit C0 control characters (PyMuPDF wraps some glyph runs in
U+0000..U+0001 markers).  SQLite's ``length()`` reports a TEXT value only up to
the first NUL, so an embedded U+0000 makes a complete document look truncated.
"""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from docsearch.core.indexer import Indexer
from docsearch.core.models import Document
from docsearch.core.repository import Repository
from docsearch.extractors.base import BaseExtractor, sanitize_text
from docsearch.extractors.pdf import PdfExtractor


class TestSanitizeText:
    def test_strips_nul(self):
        assert sanitize_text("ab\x00cd") == "abcd"

    def test_strips_pymupdf_glyph_run_markers(self):
        # Real shape observed in extracted math: "\x00T −1(x)\n\x01"
        assert sanitize_text("pu\n\x00T \u22121(x)\n\x01|det|") == "pu\nT \u22121(x)\n|det|"

    def test_keeps_layout_whitespace(self):
        kept = "a\tb\nc\rd"
        assert sanitize_text(kept) == kept

    def test_strips_other_c0_controls(self):
        assert sanitize_text("\x0c\x0e\x14\x1fx") == "x"

    def test_leaves_printable_and_unicode_alone(self):
        text = "na\u00efve \u2014 density p_X(x) \u00d7 10"
        assert sanitize_text(text) == text

    def test_empty_string(self):
        assert sanitize_text("") == ""


class _DirtyExtractor(BaseExtractor):
    """Stub extractor returning raw output containing control characters."""

    @property
    def supported_extensions(self) -> list[str]:
        return ["stub"]

    def extract_metadata(self, filepath: str) -> dict[str, Any]:
        return {"title": "Dirty"}

    def extract_text(self, filepath: str) -> str:
        return "alpha\x00beta\x01 gamma\x0c"


class TestExtractorContract:
    def test_extract_sanitizes_text(self):
        _, text = _DirtyExtractor().extract("ignored.stub")
        assert text == "alphabeta gamma"

    def test_extract_metadata_untouched(self):
        meta, _ = _DirtyExtractor().extract("ignored.stub")
        assert meta == {"title": "Dirty"}


class TestTextbookChapterExtraction:
    """Range-type chapters extract via fitz directly, bypassing the extractors."""

    def test_extract_pages_strips_controls(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import fitz

        from docsearch.core.handlers import TextbookDocumentHandler

        class FakePage:
            def __init__(self, text: str) -> None:
                self._text = text

            def get_text(self) -> str:
                return self._text

        class FakeDoc:
            def __init__(self, pages: list[FakePage]) -> None:
                self._pages = pages

            def __enter__(self) -> "FakeDoc":
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

            def close(self) -> None:
                pass

            def __len__(self) -> int:
                return len(self._pages)

            def __getitem__(self, index: int) -> FakePage:
                return self._pages[index]

        pages = [FakePage("chap one\x00marker\n"), FakePage("chap two\x01")]
        monkeypatch.setattr(fitz, "open", lambda path: FakeDoc(pages))

        repo = Repository(str(tmp_path / "t.db"), tmp_path)
        try:
            text = TextbookDocumentHandler(repo, tmp_path)._extract_pages(
                tmp_path / "book.pdf", 0, 2
            )
        finally:
            repo.close()

        assert text == "chap onemarker\n\n\nchap two"


class TestIndexingStoresCleanText:
    @pytest.fixture()
    def home(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture()
    def repo(self, home: Path):
        r = Repository(str(home / "test.db"), home)
        yield r
        r.close()

    def test_indexed_text_has_no_controls(
        self, repo: Repository, home: Path, sample_pdf_multipage: str, monkeypatch: pytest.MonkeyPatch
    ):
        dirty = "alpha\x00beta\x01 gamma\x0c"
        monkeypatch.setattr(PdfExtractor, "extract_text", lambda self, fp: dirty)

        doc = Indexer(repo, home).add_file(Path(sample_pdf_multipage).name)

        assert doc is not None
        assert doc.full_text == "alphabeta gamma"

    def test_sql_length_matches_python_length_after_indexing(
        self, repo: Repository, home: Path, sample_pdf_multipage: str, monkeypatch: pytest.MonkeyPatch
    ):
        """The reported symptom: SQL length() disagreeing with the stored value."""
        monkeypatch.setattr(PdfExtractor, "extract_text", lambda self, fp: "alpha\x00beta tail")

        doc = Indexer(repo, home).add_file(Path(sample_pdf_multipage).name)
        assert doc is not None

        conn = sqlite3.connect(str(repo.db_path))
        try:
            sql_len = conn.execute(
                "SELECT length(full_text) FROM documents WHERE path = ?", (doc.path,)
            ).fetchone()[0]
        finally:
            conn.close()

        assert sql_len == len(doc.full_text)

    def test_unsanitized_text_would_have_disagreed(self, repo: Repository):
        """Pin the underlying SQLite behaviour this fix guards against."""
        dirty = "alpha\x00beta tail"
        doc = Document(
            path="/dirty.md",
            filename="dirty.md",
            directory="/",
            extension="md",
            full_text=dirty,
        )
        doc_id = repo.upsert(doc)

        def sql_length() -> int:
            conn = sqlite3.connect(str(repo.db_path))
            try:
                return conn.execute(
                    "SELECT length(full_text) FROM documents WHERE path = ?", ("/dirty.md",)
                ).fetchone()[0]
            finally:
                conn.close()

        assert len(dirty) == 15
        assert sql_length() == 5  # counted only up to the NUL — looks truncated

        clean = sanitize_text(dirty)
        assert clean == "alphabeta tail"
        assert repo.update_document(doc_id, full_text=clean)
        assert sql_length() == len(clean) == 14
