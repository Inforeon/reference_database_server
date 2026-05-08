from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

from .models import Document
from .repository import Repository
from ..extractors import load_extractors

logger = logging.getLogger(__name__)


class Indexer:
    """Scans directories and indexes documents into the repository."""

    def __init__(self, repository: Repository):
        self.repo = repository
        self._extractors: dict[str, Any] = load_extractors()

    def _get_extractor(self, extension: str) -> Optional[Any]:
        return self._extractors.get(extension)

    def _compute_hash(self, filepath: Path) -> str:
        """Compute SHA-256 hash of a file's contents."""
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _load_sidecar(self, filepath: Path) -> dict[str, Any]:
        """Load `.meta.json` sidecar file if it exists."""
        sidecar_path = Path(str(filepath) + ".meta.json")
        if sidecar_path.is_file():
            try:
                with open(sidecar_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning("Failed to load sidecar %s: %s", sidecar_path, e)
        return {}

    def _build_document(self, filepath: Path) -> Optional[Document]:
        """Build a Document object from a file on disk."""
        suffix = filepath.suffix.lower().lstrip(".")

        if not self._get_extractor(suffix):
            return None

        try:
            stat = filepath.stat()
            content_hash = self._compute_hash(filepath)

            extractor = self._get_extractor(suffix)
            extracted_meta, full_text = extractor.extract(str(filepath))
            sidecar_meta = self._load_sidecar(filepath)

            return Document(
                path=str(filepath.resolve()),
                filename=filepath.name,
                directory=str(filepath.parent.resolve()),
                extension=suffix,
                size=stat.st_size,
                mtime=stat.st_mtime,
                content_hash=content_hash,
                extracted_metadata=extracted_meta,
                sidecar_metadata=sidecar_meta,
                full_text=full_text,
            )
        except Exception as e:
            logger.error("Failed to index %s: %s", filepath, e)
            return None

    # ── public API ───────────────────────────────────────────────

    def add_file(self, filepath: str | Path) -> Optional[Document]:
        """Index a single file. Returns the Document or None on failure."""
        p = Path(filepath).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")

        doc = self._build_document(p)
        if doc:
            self.repo.upsert(doc)
        return doc

    def remove_file(self, filepath: str | Path) -> bool:
        """Remove a single file from the index."""
        p = Path(filepath).resolve()
        return self.repo.remove(str(p))

    def scan_directory(
        self,
        dirpath: str | Path,
        recursive: bool = True,
    ) -> dict[str, int]:
        """Scan a directory tree and sync the index.

        Returns a summary dict: ``{added, updated, removed, skipped, errors}``.
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
            doc = self._build_document(Path(path_str))
            if doc:
                self.repo.upsert(doc)
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
                    new_doc = self._build_document(p)
                    if new_doc:
                        self.repo.upsert(new_doc)
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
