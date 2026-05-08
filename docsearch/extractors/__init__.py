from __future__ import annotations

from typing import Any

from .base import BaseExtractor
from .pdf import PdfExtractor
from .docx import DocxExtractor
from .markdown import MarkdownExtractor

__all__ = ["BaseExtractor", "load_extractors"]


def load_extractors() -> dict[str, BaseExtractor]:
    """Load all available extractors and build an extension→extractor map."""
    extractors: dict[str, BaseExtractor] = {}

    for ext_class in (PdfExtractor, DocxExtractor, MarkdownExtractor):
        extractor = ext_class()
        for ext in extractor.supported_extensions:
            extractors[ext] = extractor

    return extractors
