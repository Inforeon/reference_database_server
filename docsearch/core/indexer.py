from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any, Optional

from .models import Document
from .repository import Repository
from .handlers import get_handler
from ..extractors import load_extractors

logger = logging.getLogger(__name__)


class Indexer:
    """Scans directories and indexes documents into the repository.

    Delegates per-document processing to :class:`DocumentHandler` subclasses
    selected by ``document_type``.
    """

    def __init__(self, repository: Repository):
        self.repo = repository
        self._extractors: dict[str, Any] = load_extractors()

    # ── public API ───────────────────────────────────────────────

    def add_file(
        self,
        filepath: str | Path,
        document_type: str = "generic",
        extra_metadata: dict[str, Any] | None = None,
        skip_bib: bool = False,
    ) -> Optional[Document]:
        """Index a single file. Returns the Document or None on failure.

        ``document_type`` selects the handler (``"generic"``, ``"paper"``,
        ``"textbook"``, ``"reference"``).  Defaults to ``"generic"``.

        ``extra_metadata`` is a dict of user-supplied key/value pairs merged
        into the sidecar metadata (e.g. ``{"doi": "10.1234/foo"}``).

        ``skip_bib`` skips pdf2bib processing for papers (generates bibtex
        from available metadata instead).

        For ``document_type="reference"``, ``filepath`` is ignored and the
        entry is created from ``extra_metadata`` alone.
        """
        # Reference type doesn't require a file
        if document_type == "reference":
            handler = get_handler("reference", self.repo, extra_metadata=extra_metadata, skip_bib=skip_bib)
            return handler.handle(Path("."))

        p = Path(filepath).resolve()
        # Allow directories for textbook type (chapter-per-file variant)
        if document_type == "textbook":
            if not p.exists():
                raise FileNotFoundError(f"Path not found: {p}")
        elif not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")

        handler = get_handler(document_type, self.repo, extra_metadata=extra_metadata, skip_bib=skip_bib)
        return handler.handle(p)

    def remove_file(self, filepath: str | Path) -> bool:
        """Remove a single file from the index."""
        p = Path(filepath).resolve()
        return self.repo.remove(str(p))

    def move_file(
        self,
        old_filepath: str | Path,
        new_filepath: str | Path,
    ) -> Optional[Document]:
        """Move a file on disk and update its index entry.

        Moves both the source file and its ``.meta.json`` sidecar (if present)
        to the new location, then updates the database path in-place so the
        internal ``id`` is preserved.  Returns the updated Document or None
        when the source was not found.
        """
        old_p = Path(old_filepath).resolve()
        new_p = Path(new_filepath).resolve()

        doc = self.repo.get(str(old_p))
        if doc is None:
            return None

        # Create parent directories on the destination side
        new_p.parent.mkdir(parents=True, exist_ok=True)

        # Move the actual file
        shutil.move(str(old_p), str(new_p))

        # Move the sidecar metadata file if it exists
        old_sidecar = Path(str(old_p) + ".meta.json")
        new_sidecar = Path(str(new_p) + ".meta.json")
        if old_sidecar.is_file():
            shutil.move(str(old_sidecar), str(new_sidecar))

        # Update DB row in-place (preserves id)
        self.repo.rename(str(old_p), str(new_p))

        # Return the refreshed document
        return self.repo.get(str(new_p))

    def scan_directory(
        self,
        dirpath: str | Path,
        recursive: bool = True,
        document_type: str = "generic",
        extra_metadata: dict[str, Any] | None = None,
        skip_bib: bool = False,
    ) -> dict[str, int]:
        """Scan a directory tree and sync the index.

        Returns a summary dict: ``{added, updated, removed, skipped, errors}``.

        All discovered files are indexed with the given ``document_type``
        (defaults to ``"generic"``). ``extra_metadata`` is applied to every
        file in the scan. ``skip_bib`` skips pdf2bib for papers.
        """
        root = Path(dirpath).resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Not a directory: {root}")

        stats: dict[str, int] = {
            "added": 0,
            "updated": 0,
            "removed": 0,
            "skipped": 0,
            "errors": 0,
        }

        handler = get_handler(document_type, self.repo, extra_metadata=extra_metadata, skip_bib=skip_bib)

        # Collect supported files on disk
        iterator = root.rglob("*") if recursive else root.iterdir()
        disk_files: list[Path] = []

        for p in iterator:
            if not p.is_file():
                continue
            if str(p).endswith(".meta.json"):
                stats["skipped"] += 1
                continue
            ext = p.suffix.lower().lstrip(".")
            if ext in self._extractors:
                disk_files.append(p.resolve())

        disk_paths = {str(p) for p in disk_files}
        indexed_paths = set(self.repo.all_paths())

        # New files
        for path_str in disk_paths - indexed_paths:
            doc = handler.handle(Path(path_str))
            if doc:
                stats["added"] += 1
            else:
                stats["errors"] += 1

        # Changed files — check hash
        for path_str in disk_paths & indexed_paths:
            p = Path(path_str)
            try:
                current_hash = self._compute_hash(p)
                doc = self.repo.get(path_str)
                if doc and doc.content_hash != current_hash:
                    new_doc = handler.handle(p)
                    if new_doc:
                        stats["updated"] += 1
                    else:
                        stats["errors"] += 1
            except Exception:
                stats["errors"] += 1

        # Deleted files (only within scanned root)
        root_str = str(root)
        for path_str in indexed_paths - disk_paths:
            if path_str.startswith(root_str):
                self.repo.remove(path_str)
                stats["removed"] += 1

        return stats

    def needs_reindex(self, filepath: str | Path) -> bool:
        """Check whether a file is new or has been modified since last index."""
        p = Path(filepath).resolve()
        path_str = str(p)

        if not self.repo.exists(path_str):
            return True

        try:
            current_hash = self._compute_hash(p)
            doc = self.repo.get(path_str)
            return doc is None or doc.content_hash != current_hash
        except Exception:
            return True

    # ── internal helpers ────────────────────────────────────────

    @staticmethod
    def _compute_hash(filepath: Path) -> str:
        """Compute SHA-256 hash of a file's contents."""
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
