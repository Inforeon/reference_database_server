from __future__ import annotations

"""Test fixtures that generate sample documents on disk."""

import pytest
import fitz


@pytest.fixture()
def sample_pdf_with_metadata(tmp_path):
    """Create a PDF with known metadata and text content."""
    path = tmp_path / "sample.pdf"

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "This is a test document for extraction.")
    page.insert_text((72, 100), "It has multiple lines of text.")

    doc.set_metadata(
        {
            "title": "Test Document",
            "author": "Test Author",
            "subject": "Testing",
            "creator": "TestCreator",
            "producer": "TestProducer",
        }
    )
    doc.save(str(path))
    doc.close()

    return str(path)


@pytest.fixture()
def sample_pdf_no_metadata(tmp_path):
    """Create a PDF with text but no custom metadata."""
    path = tmp_path / "empty_meta.pdf"

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello world from a bare PDF.")
    doc.save(str(path))
    doc.close()

    return str(path)


@pytest.fixture()
def sample_pdf_multipage(tmp_path):
    """Create a multi-page PDF."""
    path = tmp_path / "multipage.pdf"

    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} content here.")

    doc.set_metadata({"title": "Multi Page PDF", "author": "Page Writer"})
    doc.save(str(path))
    doc.close()

    return str(path)
