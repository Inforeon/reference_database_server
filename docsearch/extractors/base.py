from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseExtractor(ABC):
    """Base class for document metadata and text extractors."""

    @property
    @abstractmethod
    def supported_extensions(self) -> list[str]:
        """Return list of file extensions this extractor handles (without dot)."""
        ...

    @abstractmethod
    def extract_metadata(self, filepath: str) -> dict[str, Any]:
        """Extract structured metadata from the file."""
        ...

    @abstractmethod
    def extract_text(self, filepath: str) -> str:
        """Extract full text content from the file."""
        ...

    def extract(self, filepath: str) -> tuple[dict[str, Any], str]:
        """Convenience method to extract both metadata and text."""
        return self.extract_metadata(filepath), self.extract_text(filepath)
