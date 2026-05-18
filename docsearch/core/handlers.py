from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from .models import Document
from .repository import Repository
from ..extractors import load_extractors

logger = logging.getLogger(__name__)


class DocumentHandler:
    """Base class for document-type-specific indexing handlers.

    Subclasses override individual steps of the pipeline to add type-specific
    behaviour (e.g. paper metadata extraction, textbook chapter chunking).
    """

    # Registered document-type slug
    document_type: str = "generic"

    def __init__(self, repository: Repository, home: str | Path) -> None:
        self.repo = repository
        self.home = Path(home).resolve()
        self._extractors: dict[str, Any] = load_extractors()
        self.extra_metadata: dict[str, Any] = {}

    # ── pipeline hooks (override in subclasses) ────────────────

    def pre_process(self, filepath: Path) -> None:
        """Called before any extraction. Use for validation or setup."""
        pass

    def _has_extractor(self, filepath: Path) -> bool:
        """Check whether an extractor is available for this file's extension."""
        ext = filepath.suffix.lower().lstrip(".")
        return ext in self._extractors

    def extract_metadata(self, filepath: Path) -> dict[str, Any]:
        """Extract structured metadata from the file.

        Default: delegate to the extension-based extractor.
        """
        ext = filepath.suffix.lower().lstrip(".")
        extractor = self._extractors.get(ext)
        if extractor:
            meta, _ = extractor.extract(str(filepath))
            return meta
        return {}

    def extract_text(self, filepath: Path) -> str:
        """Extract full-text content from the file.

        Default: delegate to the extension-based extractor.
        """
        ext = filepath.suffix.lower().lstrip(".")
        extractor = self._extractors.get(ext)
        if extractor:
            _, text = extractor.extract(str(filepath))
            return text
        return ""

    def post_process(self, doc: Document) -> Document:
        """Called after the Document is built but before upserting.

        Return the (possibly modified) Document.
        """
        return doc

    # ── internal helpers ────────────────────────────────────────

    def _compute_hash(self, filepath: Path) -> str:
        import hashlib
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _load_sidecar(self, filepath: Path) -> dict[str, Any]:
        sidecar_path = Path(str(filepath) + ".meta.json")
        if sidecar_path.is_file():
            try:
                with open(sidecar_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning("Failed to load sidecar %s: %s", sidecar_path, e)
        return {}

    def _save_sidecar(self, filepath: Path, metadata: dict[str, Any]) -> None:
        """Persist user-supplied extra metadata into the sidecar file on disk."""
        if not self.extra_metadata:
            return
        sidecar_path = Path(str(filepath) + ".meta.json")
        # Load existing sidecar first (may have been created by other means)
        data = self._load_sidecar(filepath)
        # Merge: extra_metadata overrides existing sidecar values
        data.update(self.extra_metadata)
        try:
            sidecar_path.parent.mkdir(parents=True, exist_ok=True)
            with open(sidecar_path, "w") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logger.warning("Failed to save sidecar %s: %s", sidecar_path, e)

    def _rel(self, filepath: Path) -> str:
        """Convert an absolute path to a relative path from home."""
        return str(filepath.resolve().relative_to(self.home))

    # ── public entry point ──────────────────────────────────────

    def handle(self, filepath: Path, reference: bool = False) -> Optional[Document]:
        """Run the full indexing pipeline for a single file.

        When ``reference=True``, skip all file I/O and create the Document
        from ``extra_metadata`` alone (metadata-only entry).

        Returns the indexed Document, or None on failure.
        """
        if reference:
            return self._handle_reference(filepath)

        if not self._has_extractor(filepath):
            return None

        self.pre_process(filepath)

        try:
            stat = filepath.stat()
            content_hash = self._compute_hash(filepath)

            extracted_meta = self.extract_metadata(filepath)
            full_text = self.extract_text(filepath)
            sidecar_meta = self._load_sidecar(filepath)

            # Merge user-supplied extra metadata into sidecar metadata
            merged_sidecar = {**sidecar_meta, **self.extra_metadata}

            doc = Document(
                path=self._rel(filepath),
                filename=filepath.name,
                directory=self._rel(filepath.parent),
                extension=filepath.suffix.lower().lstrip("."),
                document_type=self.document_type,
                size=stat.st_size,
                mtime=stat.st_mtime,
                content_hash=content_hash,
                extracted_metadata=extracted_meta,
                sidecar_metadata=merged_sidecar,
                full_text=full_text,
            )

            doc = self.post_process(doc)
            self.repo.upsert(doc)

            # Persist extra metadata to the sidecar file on disk
            self._save_sidecar(filepath, merged_sidecar)

            return doc
        except Exception as e:
            logger.error("Handler failed on %s: %s", filepath, e)
            return None

    def _handle_reference(self, filepath: Path) -> Optional[Document]:
        """Handle a metadata-only reference (no file on-disk file).

        Subclasses may override to add type-specific behaviour (e.g. papers
        auto-generate BibTeX). The ``filepath`` is used only for grouping
        (directory/filename derivation); it is never read.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support references")


class GenericDocumentHandler(DocumentHandler):
    """Default handler — standard extract & index."""

    document_type = "generic"

    def _handle_reference(self, filepath: Path) -> Optional[Document]:
        """Handle a metadata-only generic reference (no file on disk)."""
        try:
            merged_sidecar = dict(self.extra_metadata)

            # Derive path components (relative to home)
            fp = filepath.resolve()
            if fp.name and not fp.is_dir():
                filename = fp.name
                directory = self._rel(fp.parent)
                doc_path = self._rel(fp)
            else:
                title = merged_sidecar.get("title", "untitled")
                safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", title)
                filename = f"{safe_key}.txt"
                directory = self._rel(fp)
                doc_path = self._rel(fp / filename)

            # Build searchable full_text from metadata fields
            searchable_parts = []
            if merged_sidecar.get("title"):
                searchable_parts.append(merged_sidecar["title"])
            if merged_sidecar.get("author"):
                searchable_parts.append(str(merged_sidecar["author"]))
            if merged_sidecar.get("subject"):
                searchable_parts.append(merged_sidecar["subject"])
            if merged_sidecar.get("keywords"):
                kw = merged_sidecar["keywords"]
                if isinstance(kw, list):
                    searchable_parts.extend(str(k) for k in kw)
                else:
                    searchable_parts.append(str(kw))
            full_text = " ".join(searchable_parts)

            doc = Document(
                path=doc_path,
                filename=filename,
                directory=directory,
                extension="txt",
                document_type=self.document_type,
                source_type="reference",
                size=0,
                mtime=0.0,
                content_hash="",
                extracted_metadata={},
                sidecar_metadata=merged_sidecar,
                full_text=full_text,
            )

            doc = self.post_process(doc)
            self.repo.upsert(doc)
            return doc
        except Exception as e:
            logger.error("Generic reference handler failed: %s", e)
            return None


# ── BibTeX helpers ──────────────────────────────────────────────

def _normalize_title(title: str) -> str:
    """Normalize a title for fuzzy comparison (lowercase, strip punctuation/extra ws)."""
    title = title.lower().strip()
    # Remove common punctuation
    title = re.sub(r"[^\w\s]", "", title)
    # Collapse whitespace
    title = re.sub(r"\s+", " ", title)
    return title


def _titles_match(a: str, b: str) -> bool:
    """Check whether two titles are plausibly the same (after normalization)."""
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return False
    # Exact match after normalization
    if na == nb:
        return True
    # One contains the other (handles trailing subtitles, colons, etc.)
    if na in nb or nb in na:
        return True
    return False


def _format_author_dict(author_info: dict[str, Any]) -> str:
    """Format a single author dict (from pdf2bib) as 'Last, First'.

    Handles ``{"given": "Daniil A.", "family": "Boiko"}`` style entries.
    Falls back to string representation if keys are missing.
    """
    given = author_info.get("given", "")
    family = author_info.get("family", "")
    if given and family:
        return f"{family}, {given}"
    if family:
        return family
    if given:
        return given
    # Last resort: try to extract name from the raw dict
    return str(author_info)


def _format_authors_bib(authors_list: list[dict[str, Any]]) -> str:
    """Format a list of author dicts into a BibTeX-style author string.

    Preserves ordering from the pdf2bib ``sequence`` field when available.
    """
    # Sort by sequence if present ("first" comes before "additional")
    def sort_key(a: dict[str, Any]) -> int:
        seq = a.get("sequence", "additional")
        return 0 if seq == "first" else 1

    sorted_authors = sorted(authors_list, key=sort_key)
    parts = [_format_author_dict(a) for a in sorted_authors]
    return " and ".join(parts)


def _generate_bibtex_from_metadata(meta: dict[str, Any], citation_key: str | None = None) -> str:
    """Generate a minimal BibTeX entry from structured metadata.

    Expects keys like: title, author(s), year, journal, volume, pages, doi, url.
    Falls back gracefully when fields are missing.

    Supports ``authors_bib`` (list of dicts from pdf2bib) as well as plain
    ``author`` (string or list of strings).
    """
    entry_type = meta.get("ENTRYTYPE", "misc")
    key = citation_key or meta.get("citation_key", "unknown")

    # Map known BibTeX fields
    field_map = {
        "title": "title",
        "year": "year",
        "journal": "journal",
        "booktitle": "booktitle",
        "volume": "volume",
        "number": "number",
        "pages": "pages",
        "doi": "doi",
        "url": "url",
        "publisher": "publisher",
        "school": "school",
        "institution": "institution",
        "address": "address",
        "month": "month",
        "note": "note",
        "abstract": "abstract",
    }

    lines = [f"@{entry_type}{{{key},"]

    # Handle author field specially — supports multiple formats
    author_value = None
    if meta.get("authors_bib"):
        # pdf2bib format: list of dicts with given/family/etc.
        author_value = _format_authors_bib(meta["authors_bib"])
    elif meta.get("author"):
        raw = meta["author"]
        if isinstance(raw, list):
            author_value = " and ".join(str(v) for v in raw)
        else:
            author_value = str(raw)

    if author_value:
        lines.append(f"  author = {{{author_value}}},")

    for bib_key, meta_key in field_map.items():
        value = meta.get(meta_key) or meta.get(bib_key)
        if value:
            if isinstance(value, list):
                value = " and ".join(str(v) for v in value)
            # Escape backslashes first, then ampersands
            value = str(value).replace("\\", "\\\\").replace("&", "\\&")
            lines.append(f"  {bib_key} = {{{value}}},")

    lines.append("}")
    return "\n".join(lines)


class PaperDocumentHandler(DocumentHandler):
    """Handler for research papers using pdf2bib for bibliographic extraction.

    Workflow:
    1. If a DOI is provided in ``extra_metadata``, embed it into the PDF
       via ``pdf2doi.add_found_identifier_to_metadata`` before calling pdf2bib.
    2. Run pdf2bib to extract full bibliographic metadata.
    3. If no DOI was provided, validate that the extracted title matches the
       PDF's own metadata title; raise an error if they diverge.
    4. Store the raw bibtex string and parsed metadata (including ordered
       author list) into ``extra_metadata`` so they persist in the sidecar.

    Set ``skip_bib = True`` to skip pdf2bib entirely and generate bibtex
    from available metadata only.
    """

    document_type = "paper"
    skip_bib: bool = False

    def pre_process(self, filepath: Path) -> None:
        """Run pdf2bib (unless skipped) to enrich extra_metadata with bibliographic data."""
        if self.skip_bib:
            return

        # Step 1: Embed user-provided DOI into PDF metadata if available
        doi = self.extra_metadata.get("doi")
        if doi:
            try:
                from pdf2doi import add_found_identifier_to_metadata
                add_found_identifier_to_metadata(str(filepath), doi)
                logger.info("Embedded DOI '%s' into %s", doi, filepath)
            except Exception as e:
                logger.warning("Failed to embed DOI into %s: %s", filepath, e)

        # Step 2: Run pdf2bib
        try:
            import pdf2bib
            pdf2bib.config.set("verbose", False)
            results = pdf2bib.pdf2bib(str(filepath))
        except Exception as e:
            raise RuntimeError(
                f"pdf2bib failed on {filepath}: {e}. "
                "Try providing a DOI manually (-m doi=...) or use --skip-bib."
            ) from e

        if not results:
            raise RuntimeError(
                f"pdf2bib returned no results for {filepath}. "
                "Try providing a DOI manually (-m doi=...) or use --skip-bib."
            )

        bib_meta = results.get("metadata", {}) or {}
        bibtex_str = results.get("bibtex", "") or ""

        # Step 3: Title validation when no DOI was explicitly provided
        if not doi and bib_meta.get("title"):
            pdf_title = self._get_pdf_title(filepath)
            if pdf_title and not _titles_match(bib_meta["title"], pdf_title):
                raise RuntimeError(
                    f"Title mismatch for {filepath}: pdf2bib returned "
                    f"'{bib_meta['title']}' but PDF metadata says '{pdf_title}'. "
                    "The wrong DOI may have been detected. "
                    "Please provide the correct DOI manually (-m doi=...)."
                )

        # Step 4: Move pdf2bib author list to ``authors_bib`` to avoid
        # conflicting with the PDF's own ``author`` metadata field.
        if bib_meta.get("author"):
            bib_meta["authors_bib"] = bib_meta.pop("author")

        # Step 5: Merge bibliographic data into extra_metadata.
        # User-provided values take precedence over pdf2bib output, so we
        # apply user extras *after* the merge for any overlapping keys.
        user_overrides = {k: v for k, v in self.extra_metadata.items()
                          if k in bib_meta}
        self.extra_metadata.update(bib_meta)
        self.extra_metadata.update(user_overrides)
        if bibtex_str:
            self.extra_metadata["bibtex"] = bibtex_str

    def _get_pdf_title(self, filepath: Path) -> str:
        """Extract the title from the PDF's own metadata using PyMuPDF."""
        try:
            import fitz
            doc = fitz.open(str(filepath))
            meta = doc.metadata
            doc.close()
            return meta.get("title", "")
        except Exception:
            return ""

    def post_process(self, doc: Document) -> Document:
        """Ensure bibtex is available even if pdf2bib was skipped."""
        if self.skip_bib and not self.extra_metadata.get("bibtex"):
            # Generate bibtex from whatever metadata we have
            combined = {**doc.extracted_metadata, **doc.sidecar_metadata}
            bibtex = _generate_bibtex_from_metadata(combined)
            doc.sidecar_metadata["bibtex"] = bibtex
        return doc

    def _handle_reference(self, filepath: Path) -> Optional[Document]:
        """Handle a metadata-only paper reference (no PDF on disk).

        Skips all file-based processing (pdf2bib, DOI embedding, title
        validation). Uses ``filepath`` as a real path for identification and
        grouping — the file simply does not exist yet. Creates the Document
        from ``extra_metadata`` alone with auto-generated BibTeX.
        """
        try:
            merged_sidecar = dict(self.extra_metadata)

            # Auto-generate bibtex if not provided
            if not merged_sidecar.get("bibtex"):
                bibtex = _generate_bibtex_from_metadata(merged_sidecar)
                merged_sidecar["bibtex"] = bibtex

            # Derive citation key (stored in sidecar for BibTeX identity)
            title = merged_sidecar.get("title", "untitled")
            year = str(merged_sidecar.get("year", "0000"))
            author = merged_sidecar.get("author", merged_sidecar.get("authors_bib", "unknown"))
            if isinstance(author, list):
                if isinstance(author[0], dict):
                    author = str(author[0].get("family", "unknown"))
                else:
                    author = str(author[0])
            else:
                author = str(author)

            citation_key = merged_sidecar.get("citation_key", f"{author}{year}")
            safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", citation_key)
            if not merged_sidecar.get("citation_key"):
                merged_sidecar["citation_key"] = citation_key

            # Build searchable full_text from metadata fields
            searchable_parts = []
            if merged_sidecar.get("title"):
                searchable_parts.append(merged_sidecar["title"])
            if merged_sidecar.get("author"):
                searchable_parts.append(str(merged_sidecar["author"]))
            if merged_sidecar.get("journal"):
                searchable_parts.append(merged_sidecar["journal"])
            if merged_sidecar.get("booktitle"):
                searchable_parts.append(merged_sidecar["booktitle"])
            if merged_sidecar.get("abstract"):
                searchable_parts.append(merged_sidecar["abstract"])
            full_text = " ".join(searchable_parts)

            # Derive path components. If filepath has no meaningful name
            # (e.g. empty string resolving to cwd), use citation key.
            fp = filepath.resolve()
            if fp.name and not fp.is_dir():
                filename = fp.name
                directory = self._rel(fp.parent)
                doc_path = self._rel(fp)
            else:
                # No meaningful filename — fall back to citation key within
                # the resolved directory.
                filename = f"{safe_key}.bib"
                directory = self._rel(fp)
                doc_path = self._rel(fp / filename)

            doc = Document(
                path=doc_path,
                filename=filename,
                directory=directory,
                extension="bib",
                document_type=self.document_type,
                source_type="reference",
                size=0,
                mtime=0.0,
                content_hash="",
                extracted_metadata={},
                sidecar_metadata=merged_sidecar,
                full_text=full_text,
            )

            doc = self.post_process(doc)
            self.repo.upsert(doc)

            return doc
        except Exception as e:
            logger.error("Paper reference handler failed: %s", e)
            return None


class TextbookDocumentHandler(DocumentHandler):
    """Handler for textbooks — supports file-type (single PDF) and directory-type (chapter-per-file).

    **File-type** (single PDF, ``source_type='file'``):
    1. Extract overall PDF metadata (title, author, etc.)
    2. Upsert parent Document row (document_type="textbook", full_text = rendered TOC)
    3. Detect chapter boundaries (sidecar override > PDF TOC > single-chapter fallback)
    4. Delete any existing chapters for this textbook
    5. For each chapter: extract text slice, upsert Chapter row

    **Directory-type** (chapters as separate files, ``source_type='directory'``):
    1. Load sidecar from ``<dirname>.meta.json`` inside the directory
    2. Enumerate first-level files as chapters (alphabetical default order, overridable via sidecar)
    3. Upsert parent Document row with TOC as full_text
    4. For each chapter file: extract text + metadata, upsert Chapter row
    """

    document_type = "textbook"

    def handle(self, filepath: Path, reference: bool = False) -> Optional[Document]:
        """Run the full textbook indexing pipeline."""
        if reference:
            return self._handle_reference(filepath)
        self.pre_process(filepath)

        if filepath.is_dir():
            return self._handle_directory(filepath)
        else:
            return self._handle_file(filepath)

    # ── File-type textbook (single PDF) ───────────────────────────

    def _handle_file(self, filepath: Path) -> Optional[Document]:
        """Handle a single-PDF textbook."""
        if not self._has_extractor(filepath):
            return None

        try:
            stat = filepath.stat()
            content_hash = self._compute_hash(filepath)

            extracted_meta = self.extract_metadata(filepath)
            sidecar_meta = self._load_sidecar(filepath)

            merged_sidecar = {**sidecar_meta, **self.extra_metadata}

            chapters_info = self._detect_chapters(filepath, sidecar_meta)

            toc_lines = [extracted_meta.get("title", filepath.stem)]
            for ch in chapters_info:
                toc_lines.append(
                    f"  Ch {ch['index'] + 1}: {ch['title']} (pp. {ch['start_page']}–{ch['end_page']})"
                )
            toc_text = "\n".join(toc_lines)

            doc = Document(
                path=self._rel(filepath),
                filename=filepath.name,
                directory=self._rel(filepath.parent),
                extension=filepath.suffix.lower().lstrip("."),
                document_type=self.document_type,
                source_type="file",
                size=stat.st_size,
                mtime=stat.st_mtime,
                content_hash=content_hash,
                extracted_metadata=extracted_meta,
                sidecar_metadata=merged_sidecar,
                full_text=toc_text,
            )

            doc = self.post_process(doc)
            row_id = self.repo.upsert(doc)
            doc.id = row_id

            if doc.id is not None:
                self.repo.delete_chapters(doc.id)
                self._insert_file_chapters(filepath, doc.id, chapters_info)

            self._save_sidecar(filepath, merged_sidecar)
            return doc
        except Exception as e:
            logger.error("Textbook handler failed on %s: %s", filepath, e)
            return None

    # ── Directory-type textbook (chapter-per-file) ────────────────

    def _handle_directory(self, dirpath: Path) -> Optional[Document]:
        """Handle a directory-based textbook where each file is a chapter."""
        try:
            sidecar_meta = self._load_directory_sidecar(dirpath)
            merged_sidecar = {**sidecar_meta, **self.extra_metadata}

            chapter_files = self._enumerate_chapter_files(dirpath, sidecar_meta)

            if not chapter_files:
                logger.warning("No indexable files found in textbook directory: %s", dirpath)

            toc_lines = [merged_sidecar.get("title", dirpath.name)]
            for ch in chapter_files:
                toc_lines.append(f"  Ch {ch['index'] + 1}: {ch['title']} ({ch['file_path']})")
            toc_text = "\n".join(toc_lines)

            dir_stat = dirpath.stat()
            doc = Document(
                path=self._rel(dirpath),
                filename=dirpath.name,
                directory=self._rel(dirpath.parent),
                extension="",
                document_type=self.document_type,
                source_type="directory",
                size=dir_stat.st_size,
                mtime=dir_stat.st_mtime,
                content_hash="",
                extracted_metadata={},
                sidecar_metadata=merged_sidecar,
                full_text=toc_text,
            )

            doc = self.post_process(doc)
            row_id = self.repo.upsert(doc)
            doc.id = row_id

            if doc.id is not None:
                self.repo.delete_chapters(doc.id)
                self._insert_directory_chapters(dirpath, doc.id, chapter_files)

            self._save_directory_sidecar(dirpath, merged_sidecar)
            return doc
        except Exception as e:
            logger.error("Textbook directory handler failed on %s: %s", dirpath, e)
            return None

    def _load_directory_sidecar(self, dirpath: Path) -> dict[str, Any]:
        """Load sidecar metadata from ``<dirname>.meta.json`` inside the directory."""
        sidecar_path = dirpath / f"{dirpath.name}.meta.json"
        if sidecar_path.is_file():
            try:
                with open(sidecar_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning("Failed to load directory sidecar %s: %s", sidecar_path, e)
        return {}

    def _save_directory_sidecar(self, dirpath: Path, metadata: dict[str, Any]) -> None:
        """Persist sidecar metadata to ``<dirname>.meta.json`` inside the directory."""
        sidecar_path = dirpath / f"{dirpath.name}.meta.json"
        try:
            with open(sidecar_path, "w") as f:
                json.dump(metadata, f, indent=2)
        except IOError as e:
            logger.warning("Failed to save directory sidecar %s: %s", sidecar_path, e)

    def _enumerate_chapter_files(
        self, dirpath: Path, sidecar_meta: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """List indexable files at the first level of the directory as chapters.

        Returns a list of dicts with keys: index, title, file_path (relative).
        Default ordering is alphabetical; sidecar may override via ``chapters`` key.
        """
        # Get all first-level files that have an extractor
        all_files = sorted(
            [f for f in dirpath.iterdir() if f.is_file()],
            key=lambda p: p.name,
        )
        indexable = [f for f in all_files if self._has_extractor(f)]

        skipped = set(f.name for f in all_files if f not in set(indexable))
        if skipped:
            logger.info(
                "Skipping unsupported files in textbook directory %s: %s",
                dirpath, ", ".join(sorted(skipped)),
            )

        # Check if sidecar defines explicit chapter ordering
        if "chapters" in sidecar_meta and isinstance(sidecar_meta["chapters"], list):
            chapters = []
            for i, entry in enumerate(sidecar_meta["chapters"]):
                fname = entry.get("file", entry.get("filename", ""))
                title = entry.get("title", fname.replace(".pdf", "").replace("_", " ").title())
                chapters.append({
                    "index": entry.get("index", i),
                    "title": title,
                    "file_path": fname,
                })
            # Only include chapters whose files actually exist
            existing = {f.name for f in indexable}
            chapters = [ch for ch in chapters if ch["file_path"] in existing]
            return chapters

        # Default: alphabetical by filename
        chapters = []
        for i, f in enumerate(indexable):
            title = f.stem.replace("_", " ").replace("-", " ").title()
            chapters.append({
                "index": i,
                "title": title,
                "file_path": f.name,
            })
        return chapters

    def _insert_directory_chapters(
        self, dirpath: Path, textbook_id: int, chapter_files: list[dict[str, Any]]
    ) -> None:
        """Extract text for each chapter file and upsert into the database."""
        from .models import Chapter

        for ch_info in chapter_files:
            chapter_path = dirpath / ch_info["file_path"]
            ext = chapter_path.suffix.lower().lstrip(".")
            extractor = self._extractors.get(ext)

            if extractor:
                meta, text = extractor.extract(str(chapter_path))
            else:
                meta, text = {}, ""

            page_count = self._get_page_count(chapter_path)

            chapter = Chapter(
                textbook_id=textbook_id,
                chapter_index=ch_info["index"],
                title=ch_info["title"],
                chapter_type="file",
                start_page=None,
                end_page=None,
                page_count=page_count if page_count else None,
                file_path=ch_info["file_path"],
                metadata=meta,
                full_text=text,
            )
            self.repo.upsert_chapter(chapter)

    # ── Legacy file-type helpers (unchanged) ──────────────────────

    def _insert_file_chapters(
        self, filepath: Path, textbook_id: int, chapters_info: list[dict[str, Any]]
    ) -> None:
        """Extract text for each chapter range and upsert into the database."""
        from .models import Chapter

        for ch_info in chapters_info:
            full_text = self._extract_pages(
                filepath, ch_info["start_page"], ch_info["end_page"]
            )
            page_count = (ch_info["end_page"] or 0) - (ch_info["start_page"] or 0)
            chapter = Chapter(
                textbook_id=textbook_id,
                chapter_index=ch_info["index"],
                title=ch_info["title"],
                chapter_type="range",
                start_page=ch_info["start_page"],
                end_page=ch_info["end_page"],
                page_count=page_count if page_count else None,
                file_path=None,
                metadata={},
                full_text=full_text,
            )
            self.repo.upsert_chapter(chapter)

    def _detect_chapters(
        self, filepath: Path, sidecar_meta: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Detect chapter boundaries using sidecar override, PDF TOC, or fallback.

        All page indices are 0-based; ``end_page`` is exclusive.
        """
        # 1. Sidecar override
        if "chapters" in sidecar_meta and isinstance(sidecar_meta["chapters"], list):
            chapters = []
            for i, entry in enumerate(sidecar_meta["chapters"]):
                chapters.append({
                    "index": entry.get("index", i),
                    "title": entry.get("title", f"Chapter {i + 1}"),
                    "start_page": entry.get("start_page", 0),
                    "end_page": entry.get("end_page"),
                })
            if chapters:
                # Ensure last chapter extends to end of book
                page_count = self._get_page_count(filepath)
                if page_count and chapters[-1]["end_page"] is None:
                    chapters[-1]["end_page"] = page_count
                return chapters

        # 2. PDF TOC via PyMuPDF (already 0-based)
        toc_entries = self._parse_pdf_toc(filepath)
        if toc_entries:
            chapters = []
            for i, entry in enumerate(toc_entries):
                # end_page is exclusive — next chapter's start_page
                end_page = toc_entries[i + 1]["start_page"] if i + 1 < len(toc_entries) else self._get_page_count(filepath)
                chapters.append({
                    "index": i,
                    "title": entry["title"],
                    "start_page": entry["start_page"],
                    "end_page": end_page,
                })
            if chapters:
                return chapters

        # 3. Fallback: entire book as single chapter
        page_count = self._get_page_count(filepath)
        return [{
            "index": 0,
            "title": filepath.stem,
            "start_page": 0,
            "end_page": page_count or 0,
        }]

    def _parse_pdf_toc(self, filepath: Path) -> list[dict[str, Any]]:
        """Parse PDF table of contents via PyMuPDF, returning top-level entries only."""
        try:
            import fitz
            with fitz.open(str(filepath)) as doc:
                toc = doc.get_toc()
            # toc is list of [level, title, page, ...]
            # Take only top-level (level 1) entries
            entries = []
            for entry in toc:
                if entry[0] == 1:  # level 1 = top-level chapter
                    entries.append({
                        "title": entry[1],
                        "start_page": entry[2]-1,
                    })
            return entries
        except Exception as e:
            logger.warning("Failed to parse TOC for %s: %s", filepath, e)
            return []

    def _get_page_count(self, filepath: Path) -> int:
        """Get total page count of a PDF."""
        try:
            import fitz
            with fitz.open(str(filepath)) as doc:
                return len(doc)
        except Exception:
            return 0

    def _extract_pages(self, filepath: Path, start_page: int, end_page: int) -> str:
        """Extract text from a page range (0-based, end_page exclusive)."""
        try:
            import fitz
            with fitz.open(str(filepath)) as doc:
                pages = range(start_page, min(end_page, len(doc)))
                return "\n\n".join(doc[i].get_text() for i in pages)
        except Exception as e:
            logger.warning("Failed to extract pages %d:%d from %s: %s", start_page, end_page, filepath, e)
            return ""

    def _insert_chapters(
        self, filepath: Path, textbook_id: int, chapters_info: list[dict[str, Any]]
    ) -> None:
        """Extract text for each chapter and upsert into the database."""
        from .models import Chapter

        for ch_info in chapters_info:
            full_text = self._extract_pages(
                filepath, ch_info["start_page"], ch_info["end_page"]
            )
            chapter = Chapter(
                textbook_id=textbook_id,
                chapter_index=ch_info["index"],
                title=ch_info["title"],
                start_page=ch_info["start_page"],
                end_page=ch_info["end_page"],
                metadata={},
                full_text=full_text,
            )
            self.repo.upsert_chapter(chapter)

    def _handle_reference(self, filepath: Path) -> Optional[Document]:
        """Handle a metadata-only textbook reference (no file on disk)."""
        try:
            merged_sidecar = dict(self.extra_metadata)

            # Derive path components (relative to home)
            fp = filepath.resolve()
            if fp.name and not fp.is_dir():
                filename = fp.name
                directory = self._rel(fp.parent)
                doc_path = self._rel(fp)
            else:
                title = merged_sidecar.get("title", "untitled")
                safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", title)
                filename = f"{safe_key}.txt"
                directory = self._rel(fp)
                doc_path = self._rel(fp / filename)

            # Build searchable full_text from metadata fields
            searchable_parts = []
            if merged_sidecar.get("title"):
                searchable_parts.append(merged_sidecar["title"])
            if merged_sidecar.get("author"):
                searchable_parts.append(str(merged_sidecar["author"]))
            if merged_sidecar.get("publisher"):
                searchable_parts.append(merged_sidecar["publisher"])
            if merged_sidecar.get("edition"):
                searchable_parts.append(merged_sidecar["edition"])
            full_text = " ".join(searchable_parts)

            doc = Document(
                path=doc_path,
                filename=filename,
                directory=directory,
                extension="txt",
                document_type=self.document_type,
                source_type="reference",
                size=0,
                mtime=0.0,
                content_hash="",
                extracted_metadata={},
                sidecar_metadata=merged_sidecar,
                full_text=full_text,
            )

            doc = self.post_process(doc)
            self.repo.upsert(doc)
            return doc
        except Exception as e:
            logger.error("Textbook reference handler failed: %s", e)
            return None


# ── registry ────────────────────────────────────────────────────

_HANDLER_MAP: dict[str, type[DocumentHandler]] = {
    "generic": GenericDocumentHandler,
    "paper": PaperDocumentHandler,
    "textbook": TextbookDocumentHandler,
}


def get_handler(
    document_type: str,
    repository: Repository,
    home: str | Path,
    extra_metadata: dict[str, Any] | None = None,
    skip_bib: bool = False,
) -> DocumentHandler:
    """Return a DocumentHandler instance for the given document type.

    ``extra_metadata`` is merged into sidecar metadata during indexing.
    ``skip_bib`` skips pdf2bib processing for papers (generates bibtex from
    available metadata instead).
    """
    cls = _HANDLER_MAP.get(document_type, GenericDocumentHandler)
    handler = cls(repository, home)
    if extra_metadata:
        handler.extra_metadata = dict(extra_metadata)
    if hasattr(handler, "skip_bib"):
        handler.skip_bib = skip_bib
    return handler
