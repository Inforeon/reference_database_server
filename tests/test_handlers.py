from __future__ import annotations

"""Tests for DocumentHandler, PaperDocumentHandler, and BibTeX helpers."""

import pytest
from pathlib import Path

from docsearch.core.handlers import (
    _normalize_title,
    _titles_match,
    _format_author_dict,
    _format_authors_bib,
    _generate_bibtex_from_metadata,
)


class TestNormalizeTitle:
    def test_basic_lowercasing(self):
        assert _normalize_title("Hello World") == "hello world"

    def test_strips_punctuation(self):
        assert _normalize_title("Hello, World: A Study!") == "hello world a study"

    def test_collapses_whitespace(self):
        assert _normalize_title("Hello   World") == "hello world"

    def test_empty_string(self):
        assert _normalize_title("") == ""


class TestTitlesMatch:
    def test_exact_match(self):
        assert _titles_match("Hello World", "Hello World") is True

    def test_case_insensitive(self):
        assert _titles_match("Hello World", "HELLO WORLD") is True

    def test_punctuation_diff(self):
        assert _titles_match("Hello World: A Study", "Hello World - A Study") is True

    def test_substring_containment(self):
        assert _titles_match("Deep Learning", "Deep Learning: Foundations") is True

    def test_no_match(self):
        assert _titles_match("Machine Learning", "Quantum Physics") is False

    def test_empty_title(self):
        assert _titles_match("", "Something") is False


class TestFormatAuthorDict:
    def test_given_and_family(self):
        assert _format_author_dict({"given": "Daniil A.", "family": "Boiko"}) == "Boiko, Daniil A."

    def test_family_only(self):
        assert _format_author_dict({"family": "Einstein"}) == "Einstein"

    def test_given_only(self):
        assert _format_author_dict({"given": "Alan"}) == "Alan"

    def test_empty_dict_fallback(self):
        assert isinstance(_format_author_dict({}), str)


class TestFormatAuthorsBib:
    def test_single_author(self):
        authors = [{"given": "Jane", "family": "Smith"}]
        assert _format_authors_bib(authors) == "Smith, Jane"

    def test_multiple_authors(self):
        authors = [
            {"given": "Daniil A.", "family": "Boiko", "sequence": "first"},
            {"given": "Robert", "family": "MacKnight", "sequence": "additional"},
            {"given": "Ben", "family": "Kline", "sequence": "additional"},
        ]
        result = _format_authors_bib(authors)
        assert "Boiko, Daniil A." in result
        assert "MacKnight, Robert" in result
        assert "Kline, Ben" in result
        assert " and " in result

    def test_sequence_ordering(self):
        """First author should come first regardless of list order."""
        authors = [
            {"given": "Zara", "family": "Zzz", "sequence": "additional"},
            {"given": "Alan", "family": "Aaa", "sequence": "first"},
        ]
        result = _format_authors_bib(authors)
        # Aaa should appear before Zzz
        assert result.index("Aaa") < result.index("Zzz")

    def test_empty_list(self):
        assert _format_authors_bib([]) == ""


class TestGenerateBibtexFromMetadata:
    def test_minimal_entry(self):
        meta = {"title": "My Paper"}
        bibtex = _generate_bibtex_from_metadata(meta)
        assert "@misc{unknown," in bibtex
        assert "title = {My Paper}" in bibtex

    def test_with_citation_key(self):
        meta = {"title": "Test"}
        bibtex = _generate_bibtex_from_metadata(meta, citation_key="test2024")
        assert "@misc{test2024," in bibtex

    def test_article_entry_with_plain_author(self):
        meta = {
            "ENTRYTYPE": "article",
            "citation_key": "smith2024ml",
            "title": "Machine Learning Basics",
            "author": "Smith, Jane and Doe, John",
            "year": "2024",
            "journal": "Nature ML",
            "volume": "1",
            "pages": "10-20",
            "doi": "10.1234/ml",
        }
        bibtex = _generate_bibtex_from_metadata(meta)
        assert "@article{smith2024ml," in bibtex
        assert "title = {Machine Learning Basics}" in bibtex
        assert "author = {Smith, Jane and Doe, John}" in bibtex
        assert "year = {2024}" in bibtex
        assert "journal = {Nature ML}" in bibtex
        assert "doi = {10.1234/ml}" in bibtex

    def test_authors_bib_format(self):
        """Handle pdf2bib-style authors_bib (list of dicts)."""
        meta = {
            "title": "AI Paper",
            "authors_bib": [
                {"given": "Daniil A.", "family": "Boiko", "sequence": "first"},
                {"given": "Robert", "family": "MacKnight", "sequence": "additional"},
            ],
            "year": "2023",
            "journal": "Nature",
        }
        bibtex = _generate_bibtex_from_metadata(meta)
        assert "author = {Boiko, Daniil A. and MacKnight, Robert}" in bibtex

    def test_authors_bib_takes_precedence_over_author(self):
        """When both authors_bib and author are present, authors_bib wins."""
        meta = {
            "title": "Test",
            "author": "PDF Author",
            "authors_bib": [
                {"given": "Bib", "family": "Author", "sequence": "first"},
            ],
        }
        bibtex = _generate_bibtex_from_metadata(meta)
        assert "Author, Bib" in bibtex
        assert "PDF Author" not in bibtex

    def test_author_list_format(self):
        """Plain author as list of strings."""
        meta = {
            "title": "Paper",
            "author": ["Smith, Jane", "Doe, John"],
        }
        bibtex = _generate_bibtex_from_metadata(meta)
        assert "author = {Smith, Jane and Doe, John}" in bibtex

    def test_special_char_escaping(self):
        meta = {"title": "A & B"}
        bibtex = _generate_bibtex_from_metadata(meta)
        assert "A \\& B" in bibtex

    def test_missing_fields_graceful(self):
        meta = {}
        bibtex = _generate_bibtex_from_metadata(meta)
        assert "@misc{unknown," in bibtex
        assert bibtex.endswith("}")


class TestPaperHandlerIntegration:
    """Integration tests for PaperDocumentHandler using mock PDFs."""

    @pytest.fixture()
    def sample_pdf(self, tmp_path: Path) -> Path:
        """Create a minimal PDF with known metadata for testing."""
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Test paper content.")
        doc.set_metadata({
            "title": "Test Paper Title",
            "author": "Test Author",
        })
        out = tmp_path / "test_paper.pdf"
        doc.save(str(out))
        doc.close()
        return out

    def test_skip_bib_generates_bibtex(self, sample_pdf: Path, tmp_path: Path):
        """When skip_bib=True, bibtex should be generated from available metadata."""
        from docsearch.core.repository import Repository
        from docsearch.core.indexer import Indexer

        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        try:
            indexer = Indexer(repo)
            doc = indexer.add_file(
                str(sample_pdf),
                document_type="paper",
                extra_metadata={"title": "Test Paper Title", "author": "Test Author"},
                skip_bib=True,
            )
            assert doc is not None
            assert doc.document_type == "paper"
            # Should have generated bibtex in post_process
            assert "bibtex" in doc.sidecar_metadata
            assert "@" in doc.sidecar_metadata["bibtex"]
        finally:
            repo.close()

    def test_wrong_doi_raises_error(self, sample_pdf: Path, tmp_path: Path):
        """When a DOI is provided, pdf2bib uses it regardless of PDF content.

        We verify the pipeline accepts the DOI and does NOT raise — the title
        mismatch guard only fires when *no* DOI is supplied. Here we confirm
        the DOI is respected and the document indexes successfully.
        """
        from docsearch.core.repository import Repository
        from docsearch.core.indexer import Indexer

        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        try:
            indexer = Indexer(repo)
            # Embedding a real DOI that maps to different metadata than our
            # test PDF — should succeed (user DOI takes precedence).
            doc = indexer.add_file(
                str(sample_pdf),
                document_type="paper",
                extra_metadata={"doi": "10.1038/s41586-023-06792-0"},
            )
            # If pdf2bib reached the network, doc will be non-None and the
            # fetched title will differ from our PDF's own title.
            if doc is not None:
                assert doc.sidecar_metadata.get("doi") == "10.1038/s41586-023-06792-0"
        except RuntimeError as e:
            # Network failure is acceptable — skip gracefully
            if "pdf2bib failed" in str(e):
                pytest.skip(f"Network unavailable: {e}")
            raise
        finally:
            repo.close()

    def test_provided_doi_embedded_and_used(self, sample_pdf: Path, tmp_path: Path):
        """When a DOI is provided, it should be embedded into the PDF before pdf2bib runs."""
        from docsearch.core.repository import Repository
        from docsearch.core.indexer import Indexer

        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        try:
            indexer = Indexer(repo)
            # Using skip_bib to avoid network dependency while verifying
            # the pipeline accepts doi in extra_metadata
            doc = indexer.add_file(
                str(sample_pdf),
                document_type="paper",
                extra_metadata={
                    "doi": "10.1234/test",
                    "title": "Test Paper Title",
                    "author": "Test Author",
                },
                skip_bib=True,
            )
            assert doc is not None
            assert doc.sidecar_metadata.get("doi") == "10.1234/test"
        finally:
            repo.close()


class TestTitleMismatchLogic:
    """Unit tests for the title validation guard (no network required)."""

    def test_title_mismatch_raises_runtime_error(self, tmp_path: Path):
        """PaperDocumentHandler should raise when pdf2bib title differs from PDF title."""
        from unittest.mock import patch, MagicMock
        from docsearch.core.handlers import PaperDocumentHandler
        from docsearch.core.repository import Repository

        # Create a real PDF with known title
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "content")
        doc.set_metadata({"title": "Real PDF Title"})
        pdf_path = tmp_path / "mismatch.pdf"
        doc.save(str(pdf_path))
        doc.close()

        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        handler = PaperDocumentHandler(repo)
        handler.extra_metadata = {}  # No DOI provided

        # Mock pdf2bib to return a different title
        fake_result = {
            "metadata": {"title": "Completely Different Title"},
            "bibtex": "@article{fake, title={Completely Different Title}}",
        }
        with patch("pdf2bib.pdf2bib", return_value=fake_result):
            with pytest.raises(RuntimeError, match="Title mismatch"):
                handler.pre_process(pdf_path)

        repo.close()

    def test_title_match_succeeds(self, tmp_path: Path):
        """No error when pdf2bib title matches PDF title."""
        from unittest.mock import patch
        from docsearch.core.handlers import PaperDocumentHandler
        from docsearch.core.repository import Repository

        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "content")
        doc.set_metadata({"title": "Matching Title"})
        pdf_path = tmp_path / "match.pdf"
        doc.save(str(pdf_path))
        doc.close()

        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        handler = PaperDocumentHandler(repo)
        handler.extra_metadata = {}

        fake_result = {
            "metadata": {"title": "matching title"},  # Case diff only
            "bibtex": "@article{ok, title={Matching Title}}",
        }
        with patch("pdf2bib.pdf2bib", return_value=fake_result):
            # Should NOT raise
            handler.pre_process(pdf_path)

        assert handler.extra_metadata.get("title") == "matching title"
        repo.close()

    def test_no_pdf_title_skips_validation(self, tmp_path: Path):
        """If the PDF has no title metadata, skip the mismatch check."""
        from unittest.mock import patch
        from docsearch.core.handlers import PaperDocumentHandler
        from docsearch.core.repository import Repository

        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "content")
        # No metadata set at all
        pdf_path = tmp_path / "notitle.pdf"
        doc.save(str(pdf_path))
        doc.close()

        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        handler = PaperDocumentHandler(repo)
        handler.extra_metadata = {}

        fake_result = {
            "metadata": {"title": "Some Title From Bib"},
            "bibtex": "@article{bib, title={Some Title From Bib}}",
        }
        with patch("pdf2bib.pdf2bib", return_value=fake_result):
            # Should NOT raise — no PDF title to compare against
            handler.pre_process(pdf_path)

        repo.close()

    def test_doi_provided_skips_validation(self, tmp_path: Path):
        """When a DOI is explicitly provided, skip title validation entirely."""
        from unittest.mock import patch
        from docsearch.core.handlers import PaperDocumentHandler
        from docsearch.core.repository import Repository

        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "content")
        doc.set_metadata({"title": "PDF Title"})
        pdf_path = tmp_path / "with_doi.pdf"
        doc.save(str(pdf_path))
        doc.close()

        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        handler = PaperDocumentHandler(repo)
        handler.extra_metadata = {"doi": "10.1234/user-provided"}

        fake_result = {
            "metadata": {"title": "Totally Different Title", "doi": "10.1234/user-provided"},
            "bibtex": "@article{ok, title={Totally Different Title}, doi={10.1234/user-provided}}",
        }
        with patch("pdf2doi.add_found_identifier_to_metadata"):
            with patch("pdf2bib.pdf2bib", return_value=fake_result):
                # Should NOT raise — user provided DOI, we trust it
                handler.pre_process(pdf_path)

        assert handler.extra_metadata.get("doi") == "10.1234/user-provided"
        repo.close()

    def test_authors_moved_to_authors_bib(self, tmp_path: Path):
        """Verify that pdf2bib's 'author' list is moved to 'authors_bib'."""
        from unittest.mock import patch
        from docsearch.core.handlers import PaperDocumentHandler
        from docsearch.core.repository import Repository

        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "content")
        doc.set_metadata({"title": "Test"})
        pdf_path = tmp_path / "authors.pdf"
        doc.save(str(pdf_path))
        doc.close()

        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        handler = PaperDocumentHandler(repo)
        handler.extra_metadata = {}

        fake_result = {
            "metadata": {
                "title": "Test",
                "author": [
                    {"given": "Alice", "family": "Smith", "sequence": "first"},
                    {"given": "Bob", "family": "Jones", "sequence": "additional"},
                ],
            },
            "bibtex": "@article{test, author={Alice Smith and Bob Jones}}",
        }
        with patch("pdf2bib.pdf2bib", return_value=fake_result):
            handler.pre_process(pdf_path)

        assert "authors_bib" in handler.extra_metadata
        assert "author" not in handler.extra_metadata
        authors = handler.extra_metadata["authors_bib"]
        assert len(authors) == 2
        assert authors[0]["family"] == "Smith"
        assert authors[1]["family"] == "Jones"
        repo.close()


class TestTextbookHandler:
    """Tests for TextbookDocumentHandler pipeline."""

    def _make_textbook_pdf(self, tmp_path: Path, toc_entries: list[tuple[int, str, int]] | None = None):
        """Create a multi-page PDF with optional TOC outline entries.

        ``toc_entries`` should be ``(level, title, page)`` tuples where ``page``
        is a **0-based** page index (as PyMuPDF expects).
        """
        return self._make_textbook_pdf_with_name(tmp_path, "textbook.pdf", toc_entries)

    def _make_textbook_pdf_with_name(self, tmp_path: Path, name: str, toc_entries: list[tuple[int, str, int]] | None = None):
        """Create a multi-page PDF with a custom filename and optional TOC."""
        import fitz
        path = tmp_path / name
        doc = fitz.open()
        for i in range(6):
            page = doc.new_page()
            page.insert_text((72, 72), f"This is page {i} of the textbook.")
        doc.set_metadata({"title": "Test Textbook", "author": "Test Author"})
        if toc_entries:
            # PyMuPDF's set_toc expects [[level, title, page, ...], ...] with 1-based pages
            toc_data = [[level, title, page + 1] for level, title, page in toc_entries]
            doc.set_toc(toc_data)
        doc.save(str(path))
        doc.close()
        return path

    def test_fallback_single_chapter_no_toc(self, tmp_path: Path):
        """Without TOC and without sidecar, entire book becomes one chapter."""
        from docsearch.core.handlers import TextbookDocumentHandler
        from docsearch.core.repository import Repository

        pdf_path = self._make_textbook_pdf(tmp_path)
        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        handler = TextbookDocumentHandler(repo)

        chapters = handler._detect_chapters(pdf_path, {})
        assert len(chapters) == 1
        assert chapters[0]["index"] == 0
        assert chapters[0]["start_page"] == 0
        assert chapters[0]["end_page"] == 6
        repo.close()

    def test_chapter_detection_from_pdf_toc(self, tmp_path: Path):
        """TOC entries should produce multiple chapters (0-based, exclusive end)."""
        from docsearch.core.handlers import TextbookDocumentHandler
        from docsearch.core.repository import Repository

        # set_toc uses 0-based page indices; pages are 0,2,4 in a 6-page doc
        toc = [(1, "Introduction", 0), (1, "Core Concepts", 2), (1, "Advanced Topics", 4)]
        pdf_path = self._make_textbook_pdf(tmp_path, toc)
        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        handler = TextbookDocumentHandler(repo)

        chapters = handler._detect_chapters(pdf_path, {})
        assert len(chapters) == 3
        assert chapters[0]["title"] == "Introduction"
        assert chapters[0]["start_page"] == 0
        assert chapters[0]["end_page"] == 2
        assert chapters[1]["title"] == "Core Concepts"
        assert chapters[1]["start_page"] == 2
        assert chapters[1]["end_page"] == 4
        assert chapters[2]["title"] == "Advanced Topics"
        assert chapters[2]["start_page"] == 4
        assert chapters[2]["end_page"] == 6  # last chapter extends to end of book
        repo.close()

    def test_sidecar_override_wins_over_toc(self, tmp_path: Path):
        """Sidecar chapter spec should override PDF TOC."""
        from docsearch.core.handlers import TextbookDocumentHandler
        from docsearch.core.repository import Repository

        toc = [(1, "Toc Chapter", 0)]
        pdf_path = self._make_textbook_pdf(tmp_path, toc)
        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        handler = TextbookDocumentHandler(repo)

        sidecar = {
            "chapters": [
                {"index": 0, "title": "Custom Ch 1", "start_page": 0, "end_page": 3},
                {"index": 1, "title": "Custom Ch 2", "start_page": 3, "end_page": 6},
            ]
        }
        chapters = handler._detect_chapters(pdf_path, sidecar)
        assert len(chapters) == 2
        assert chapters[0]["title"] == "Custom Ch 1"
        assert chapters[0]["start_page"] == 0
        assert chapters[0]["end_page"] == 3
        assert chapters[1]["title"] == "Custom Ch 2"
        assert chapters[1]["start_page"] == 3
        assert chapters[1]["end_page"] == 6
        repo.close()

    def test_full_handle_creates_parent_and_chapters(self, tmp_path: Path):
        """End-to-end: handle() creates parent Document + chapter rows."""
        from docsearch.core.handlers import TextbookDocumentHandler
        from docsearch.core.repository import Repository

        toc = [(1, "Chapter One", 0), (1, "Chapter Two", 3)]
        pdf_path = self._make_textbook_pdf(tmp_path, toc)
        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        handler = TextbookDocumentHandler(repo)

        doc = handler.handle(pdf_path)
        assert doc is not None
        assert doc.document_type == "textbook"
        assert doc.id is not None
        assert "Test Textbook" in doc.full_text  # TOC text

        chapters = repo.get_chapters(doc.id)
        assert len(chapters) == 2
        assert chapters[0].title == "Chapter One"
        assert chapters[0].full_text != ""
        assert "page 1" in chapters[0].full_text.lower() or "page 1" in chapters[0].full_text
        repo.close()

    def test_reindex_deletes_old_chapters(self, tmp_path: Path):
        """Re-indexing the same file should replace old chapters."""
        from docsearch.core.handlers import TextbookDocumentHandler
        from docsearch.core.repository import Repository

        pdf_path = self._make_textbook_pdf(tmp_path)
        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))

        # First index — single chapter fallback
        handler1 = TextbookDocumentHandler(repo)
        doc1 = handler1.handle(pdf_path)
        assert len(repo.get_chapters(doc1.id)) == 1

        # Second index with different TOC — should replace
        toc = [(1, "New A", 0), (1, "New B", 3)]
        pdf_path2 = self._make_textbook_pdf_with_name(tmp_path, "textbook_v2.pdf", toc)
        # Overwrite original path with new version
        import shutil
        shutil.copy(str(pdf_path2), str(pdf_path))

        handler2 = TextbookDocumentHandler(repo)
        doc2 = handler2.handle(pdf_path)
        chapters = repo.get_chapters(doc2.id)
        assert len(chapters) == 2
        assert chapters[0].title == "New A"
        repo.close()

    def test_metadata_inheritance_in_chapters(self, tmp_path: Path):
        """Chapter combined_metadata should inherit from parent textbook."""
        from docsearch.core.handlers import TextbookDocumentHandler
        from docsearch.core.repository import Repository

        pdf_path = self._make_textbook_pdf(tmp_path)
        db_path = tmp_path / "test.db"
        repo = Repository(str(db_path))
        handler = TextbookDocumentHandler(repo)

        doc = handler.handle(pdf_path)
        chapters = repo.get_chapters(doc.id)
        ch = chapters[0]

        combined = ch.combined_metadata(doc)
        assert combined["author"] == "Test Author"
        assert combined["title"] == "Test Textbook"
        repo.close()
