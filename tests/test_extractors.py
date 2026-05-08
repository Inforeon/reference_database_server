from __future__ import annotations

"""Tests for PdfExtractor."""

import pytest

from docsearch.extractors.pdf import PdfExtractor


@pytest.fixture()
def extractor():
    return PdfExtractor()


class TestPdfExtractor:
    def test_supported_extensions(self, extractor: PdfExtractor):
        assert extractor.supported_extensions == ["pdf"]

    def test_extract_metadata_full(self, extractor: PdfExtractor, sample_pdf_with_metadata: str):
        meta = extractor.extract_metadata(sample_pdf_with_metadata)

        assert meta["title"] == "Test Document"
        assert meta["author"] == "Test Author"
        assert meta["subject"] == "Testing"
        assert meta["creator"] == "TestCreator"
        assert meta["producer"] == "TestProducer"
        assert meta["page_count"] == 1

    def test_extract_metadata_minimal(self, extractor: PdfExtractor, sample_pdf_no_metadata: str):
        meta = extractor.extract_metadata(sample_pdf_no_metadata)

        # page_count should always be present
        assert meta.get("page_count") == 1
        # custom metadata fields may be absent or empty
        assert not meta.get("title")
        assert not meta.get("author")

    def test_extract_text_single_page(self, extractor: PdfExtractor, sample_pdf_with_metadata: str):
        text = extractor.extract_text(sample_pdf_with_metadata)

        assert "This is a test document for extraction." in text
        assert "It has multiple lines of text." in text

    def test_extract_text_multipage(self, extractor: PdfExtractor, sample_pdf_multipage: str):
        text = extractor.extract_text(sample_pdf_multipage)

        assert "Page 1 content here." in text
        assert "Page 2 content here." in text
        assert "Page 3 content here." in text

    def test_extract_returns_both(self, extractor: PdfExtractor, sample_pdf_with_metadata: str):
        meta, text = extractor.extract(sample_pdf_with_metadata)

        assert isinstance(meta, dict)
        assert isinstance(text, str)
        assert meta["title"] == "Test Document"
        assert "test document" in text.lower()

    def test_extract_metadata_nonexistent_file(self, extractor: PdfExtractor):
        meta = extractor.extract_metadata("/nonexistent/path/file.pdf")
        assert meta == {}

    def test_extract_text_nonexistent_file(self, extractor: PdfExtractor):
        text = extractor.extract_text("/nonexistent/path/file.pdf")
        assert text == ""

    def test_extract_nonexistent_file(self, extractor: PdfExtractor):
        meta, text = extractor.extract("/nonexistent/path/file.pdf")
        assert meta == {}
        assert text == ""

    def test_multipage_page_count(self, extractor: PdfExtractor, sample_pdf_multipage: str):
        meta = extractor.extract_metadata(sample_pdf_multipage)
        assert meta["page_count"] == 3
