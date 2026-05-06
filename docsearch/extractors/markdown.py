from __future__ import annotations

from typing import Any

import yaml

from docsearch.extractors.base import BaseExtractor


class MarkdownExtractor(BaseExtractor):
    """Extractor for Markdown files with YAML frontmatter support."""

    @property
    def supported_extensions(self) -> list[str]:
        """Return list of file extensions this extractor handles."""
        return ["md", "markdown", "txt"]

    def extract_metadata(self, filepath: str) -> dict[str, Any]:
        """Extract YAML frontmatter metadata from a Markdown file."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            if not content.startswith("---"):
                return {}

            end_idx = content.index("---", 3)
            frontmatter = content[3:end_idx].strip()

            if not frontmatter:
                return {}

            parsed = yaml.safe_load(frontmatter)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def extract_text(self, filepath: str) -> str:
        """Extract body text from a Markdown file, stripping frontmatter if present."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            if content.startswith("---"):
                end_idx = content.index("---", 3)
                return content[end_idx + 3:].strip()

            return content
        except Exception:
            return ""
