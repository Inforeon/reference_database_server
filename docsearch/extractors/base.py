from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any


# C0 control characters, excluding the whitespace controls that carry meaning:
# tab (0x09), newline (0x0a) and carriage return (0x0d).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize_text(text: str) -> str:
    """Strip control characters from extracted text.

    PDF extraction can emit C0 controls — PyMuPDF wraps some glyph runs in
    U+0000..U+0001 markers — and those survive into the stored ``full_text``.
    A single embedded NUL is actively harmful: SQLite's ``length()`` reports a
    TEXT value only up to the first NUL, so a complete document reads as
    truncated to anyone measuring it in SQL.

    Tab, newline and carriage return are preserved; layout whitespace is part of
    the text.
    """
    return _CONTROL_CHARS.sub("", text)


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
        """Extract full text content from the file.

        Implementations return raw extractor output; control characters are
        stripped by :meth:`extract`, which is the path callers should use.
        """
        ...

    def extract(self, filepath: str) -> tuple[dict[str, Any], str]:
        """Convenience method to extract both metadata and text.

        Text is passed through :func:`sanitize_text`.
        """
        return self.extract_metadata(filepath), sanitize_text(self.extract_text(filepath))
