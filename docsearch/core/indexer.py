from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any, Optional

from .models import Document
from .repository import Repository
from .handlers import get_handler
from .sidecars import SIDECAR_SUFFIX, load_sidecar, sidecar_path, write_sidecar
from ..extractors import load_extractors

logger = logging.getLogger(__name__)


class Indexer:
    """Scans directories and indexes documents into the repository.

    Delegates per-document processing to :class:`DocumentHandler` subclasses
    selected by ``document_type``.  All paths stored in the database are
    relative to ``home``; absolute paths are resolved only for filesystem
    operations.
    """

    def __init__(self, repository: Repository, home: str | Path):
        self.repo = repository
        self.home = Path(home).resolve()
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
        """
        p = (self.home / filepath).resolve()
        # Allow directories for textbook and paper types
        if document_type in ("textbook", "paper"):
            if not p.exists():
                raise FileNotFoundError(f"Path not found: {p}")
        elif not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")

        handler = get_handler(document_type, self.repo, self.home, extra_metadata=extra_metadata, skip_bib=skip_bib)
        return handler.handle(p)

    def add_reference(
        self,
        filepath: str | Path,
        document_type: str = "paper",
        extra_metadata: dict[str, Any] | None = None,
        skip_bib: bool = False,
    ) -> Optional[Document]:
        """Index a metadata-only reference (no file required).

        ``filepath`` is a real path used for identification and grouping within
        the database home. The file need not exist; if it is later placed at
        that path, a normal ``add_file`` upsert will enrich the entry.

        ``document_type`` selects the handler (e.g. ``"paper"``). Defaults to
        ``"paper"`` since references are most commonly bibliographic entries.

        ``extra_metadata`` supplies all metadata for the reference (title,
        author, year, journal, doi, etc.). At minimum a ``title`` is expected.

        ``skip_bib`` has no effect for references (BibTeX is always generated
        from metadata when not provided).
        """
        p = (self.home / filepath).resolve()
        # Ensure parent directories exist so the path resolves cleanly
        p.parent.mkdir(parents=True, exist_ok=True)

        handler = get_handler(document_type, self.repo, self.home, extra_metadata=extra_metadata, skip_bib=skip_bib)
        return handler.handle(p, reference=True)

    def remove_file(self, filepath: str | Path) -> bool:
        """Remove a single file from the index."""
        p = (self.home / filepath).resolve()
        rel = str(p.relative_to(self.home))
        return self.repo.remove(rel)

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
        old_p = (self.home / old_filepath).resolve()
        new_p = (self.home / new_filepath).resolve()
        old_rel = str(old_p.relative_to(self.home))
        new_rel = str(new_p.relative_to(self.home))

        doc = self.repo.get(old_rel)
        if doc is None:
            return None

        # For reference-only entries there is no physical file to move.
        if doc.source_type != "reference":
            # Create parent directories on the destination side
            new_p.parent.mkdir(parents=True, exist_ok=True)

            # Move the actual file
            shutil.move(str(old_p), str(new_p))

            # Move the sidecar metadata file if it exists
            old_sidecar = sidecar_path(old_p, doc.source_type)
            new_sidecar = sidecar_path(new_p, doc.source_type)
            if old_sidecar.is_file():
                shutil.move(str(old_sidecar), str(new_sidecar))

        # Update DB row in-place (preserves id)
        self.repo.rename(old_rel, new_rel)

        # Return the refreshed document
        return self.repo.get(new_rel)

    def attach_file(
        self,
        filepath: str | Path,
        doc_id: int,
        document_type: str = "generic",
        existing_metadata: dict[str, Any] | None = None,
    ) -> Optional[Document]:
        """Attach a physical file to an existing reference-only entry.

        Steps:
        1. Update the DB path from the old reference path to the new file location.
        2. Write ``existing_metadata`` into the sidecar so stored data overrides
           anything extracted from the uploaded file.
        3. Call ``add_file`` to extract metadata and populate full_text.

        Returns the updated Document or None on failure.
        """
        p = (self.home / filepath).resolve()

        if not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")

        rel = str(p.relative_to(self.home))

        # Get the current reference entry by id to find its old path
        old_doc = self.repo.get_by_id(doc_id)
        if old_doc is None:
            return None

        old_rel = old_doc.path

        # 1. Move the DB path to the new location (no filesystem move — file already there)
        self.repo.rename(old_rel, rel)

        # 2. Write preserved metadata into sidecar so it takes precedence
        if existing_metadata:
            write_sidecar(sidecar_path(p), existing_metadata)

        # 3. Re-index: extract file metadata + load sidecar (sidecar wins).
        # Pass skip_bib=True because bibliographic data is already preserved
        # in the sidecar; we don't want pdf2bib to fail on non-pdf files.
        return self.add_file(rel, document_type=document_type, skip_bib=True)

    def convert_to_directory(
        self,
        doc_id: int,
        supplement_path: str | Path,
        supplement_name: str | None = None,
    ) -> Optional[Document]:
        """Convert a file-type paper to directory-type by adding a supplement.

        Creates a directory from the paper's filename, moves the PDF into it
        as the primary, moves the supplement into the directory, writes the
        sidecar, and re-indexes as a directory-type paper.

        Returns the updated Document or None on failure.
        """
        sup_p = (self.home / supplement_path).resolve()
        if not sup_p.is_file():
            raise FileNotFoundError(f"Supplement file not found: {sup_p}")

        doc = self.repo.get_by_id(doc_id)
        if doc is None:
            return None

        if doc.source_type == "directory":
            # Already a directory — just add the supplement directly
            return self._add_supplement_to_directory(doc, sup_p, supplement_name)

        if doc.source_type == "reference":
            raise ValueError("Cannot convert reference-type paper to directory")

        # Resolve the current file path
        old_p = (self.home / doc.path).resolve()
        if not old_p.is_file():
            logger.error("Paper file not found at %s", old_p)
            return None

        # Create directory from paper filename (without extension)
        new_dir = old_p.parent / doc.filename.rsplit(".", 1)[0]
        new_dir.mkdir(parents=True, exist_ok=True)

        # Move the primary PDF into the directory
        primary_name = old_p.name
        shutil.move(str(old_p), str(new_dir / primary_name))

        # Move the sidecar if it exists
        old_sidecar = sidecar_path(old_p, doc.source_type)
        new_sidecar = sidecar_path(new_dir, "directory")
        if old_sidecar.is_file():
            shutil.move(str(old_sidecar), str(new_sidecar))

        # Move the supplement into the directory
        sup_filename = supplement_name or sup_p.name
        shutil.move(str(sup_p), str(new_dir / sup_filename))

        # Write sidecar with primary and supplements info
        existing_meta = load_sidecar(new_sidecar)
        existing_meta["primary"] = primary_name
        existing_meta["supplements"] = {
            "0": {"file": sup_filename, "name": sup_filename.replace(".pdf", "").replace("_", " ").title()}
        }
        write_sidecar(new_sidecar, existing_meta)

        # Update DB path and source_type
        new_rel = str(new_dir.relative_to(self.home))
        self.repo.rename(doc.path, new_rel)
        self.repo.update_document(doc_id, source_type="directory")

        # Re-index as directory-type paper
        return self.add_file(new_rel, document_type="paper", skip_bib=True)

    def _add_supplement_to_directory(
        self, doc: "Document", supplement_path: Path, supplement_name: str | None
    ) -> Optional[Document]:
        """Add a supplement to an existing directory-type paper."""
        from .handlers import get_handler

        if not supplement_path.is_file():
            raise FileNotFoundError(f"Supplement file not found: {supplement_path}")

        # Get the directory path
        dir_p = (self.home / doc.path).resolve()
        if not dir_p.is_dir():
            logger.error("Paper directory not found at %s", dir_p)
            return None

        # Copy supplement into directory (don't move — file might be elsewhere)
        sup_filename = supplement_name or supplement_path.name
        shutil.copy2(str(supplement_path), str(dir_p / sup_filename))

        # Update sidecar to include new supplement
        sidecar = sidecar_path(dir_p, "directory")
        meta = load_sidecar(sidecar)
        supplements = meta.get("supplements", {})
        new_index = len(supplements)
        supplements[str(new_index)] = {
            "file": sup_filename,
            "name": sup_filename.replace(".pdf", "").replace("_", " ").title(),
        }
        meta["supplements"] = supplements
        write_sidecar(sidecar, meta)

        # Re-index the directory to pick up the new supplement
        return self.add_file(doc.path, document_type="paper", skip_bib=True)

    # ── metadata editing ────────────────────────────────────────

    def set_metadata_key(self, doc_id: int, key: str, value: Any) -> bool:
        """Set one sidecar metadata key on a document, in the DB and on disk.

        Both stores are written so they converge: the column is what search,
        tag filters and every read path use, while the ``.meta.json`` file is
        what re-indexing reads back — an edit applied to only one of them is
        either invisible or transient.  Neither the document nor its text is
        re-extracted, so a one-key edit costs nothing regardless of file size.

        The new value is applied on top of the stored column merged with the
        file (file winning), so hand-edited sidecars — a documented workflow —
        and DB-only keys both survive an edit to an unrelated key.

        Returns False if no such document exists.  A sidecar that cannot be
        written (e.g. a read-only database home) is logged, not raised: the
        database edit has already committed and must not be undone.
        """
        return self._edit_metadata(doc_id, patch={key: value})

    def delete_metadata_key(self, doc_id: int, key: str) -> bool:
        """Remove one sidecar metadata key from a document, in DB and on disk.

        Returns False if no such document exists; True when the key was absent
        to begin with, since both stores end up in the requested state.
        """
        return self._edit_metadata(doc_id, remove_keys=[key])

    def metadata_sidecar_path(self, doc: Document) -> Path:
        """Absolute path of the sidecar file backing ``doc``."""
        return sidecar_path((self.home / doc.path).resolve(), doc.source_type)

    def _edit_metadata(
        self,
        doc_id: int,
        *,
        patch: dict[str, Any] | None = None,
        remove_keys: list[str] | None = None,
    ) -> bool:
        doc = self.repo.get_by_id(doc_id)
        if doc is None:
            return False

        sidecar = self.metadata_sidecar_path(doc)
        merged: dict[str, Any] = {**doc.sidecar_metadata, **load_sidecar(sidecar)}
        if patch:
            merged.update(patch)
        for key in remove_keys or []:
            merged.pop(key, None)

        updated = self.repo.update_sidecar_metadata(
            doc_id, patch=merged, remove_keys=remove_keys
        )
        if not updated:
            return False

        write_sidecar(sidecar, merged)
        return True

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
        root = (self.home / dirpath).resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Not a directory: {root}")

        stats: dict[str, int] = {
            "added": 0,
            "updated": 0,
            "removed": 0,
            "skipped": 0,
            "errors": 0,
        }

        handler = get_handler(document_type, self.repo, self.home, extra_metadata=extra_metadata, skip_bib=skip_bib)

        # Collect supported files on disk
        iterator = root.rglob("*") if recursive else root.iterdir()
        disk_files: list[Path] = []

        for p in iterator:
            if not p.is_file():
                continue
            if str(p).endswith(SIDECAR_SUFFIX):
                stats["skipped"] += 1
                continue
            ext = p.suffix.lower().lstrip(".")
            if ext in self._extractors:
                disk_files.append(p.resolve())

        # Use relative paths for DB comparisons
        def to_rel(p: Path) -> str:
            return str(p.relative_to(self.home))

        disk_rels = {to_rel(p) for p in disk_files}
        indexed_paths = set(self.repo.all_paths())

        # New files
        for rel_str in disk_rels - indexed_paths:
            doc = handler.handle(self.home / rel_str)
            if doc:
                stats["added"] += 1
            else:
                stats["errors"] += 1

        # Changed files — check hash
        for rel_str in disk_rels & indexed_paths:
            abs_p = self.home / rel_str
            try:
                current_hash = self._compute_hash(abs_p)
                doc = self.repo.get(rel_str)
                if doc and doc.content_hash != current_hash:
                    new_doc = handler.handle(abs_p)
                    if new_doc:
                        stats["updated"] += 1
                    else:
                        stats["errors"] += 1
            except Exception:
                stats["errors"] += 1

        # Deleted files (only within scanned root)
        root_rel = to_rel(root)
        for path_str in indexed_paths - disk_rels:
            if path_str == root_rel or path_str.startswith(root_rel + "/"):
                self.repo.remove(path_str)
                stats["removed"] += 1

        return stats

    def needs_reindex(self, filepath: str | Path) -> bool:
        """Check whether a file is new or has been modified since last index."""
        p = (self.home / filepath).resolve()
        rel = str(p.relative_to(self.home))

        if not self.repo.exists(rel):
            return True

        try:
            current_hash = self._compute_hash(p)
            doc = self.repo.get(rel)
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
