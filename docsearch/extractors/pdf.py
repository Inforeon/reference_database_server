from __future__ import annotations

from typing import Any

import fitz

from docsearch.extractors.base import BaseExtractor


class PdfExtractor(BaseExtractor):
    """Extractor for PDF documents using PyMuPDF."""

    @property
    def supported_extensions(self) -> list[str]:
        """Return list of file extensions this extractor handles."""
        return ["pdf"]

    def extract_metadata(self, filepath: str) -> dict[str, Any]:
        """Extract structured metadata from a PDF file."""
        try:
            with fitz.open(filepath) as doc:
                metadata = doc.metadata or {}
                result = {}
                for key in ("title", "author", "subject", "creator", "producer", "creationDate", "modDate"):
                    value = metadata.get(key)
                    if value is not None:
                        result[key] = value
                result["page_count"] = len(doc)
                return result
        except Exception:
            return {}

    def extract_text(self, filepath: str) -> str:
        """Extract full text content from a PDF file."""
        try:
            with fitz.open(filepath) as doc:
                return "\n\n".join(page.get_text() for page in doc)
        except Exception:
            return ""
