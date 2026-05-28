from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import click

from docsearch.core.indexer import Indexer
from docsearch.core.models import Chapter
from docsearch.core.repository import Repository
from docsearch.extractors import load_extractors


@click.group(name="textbooks")
def textbooks() -> None:
    """Manage textbooks (add, upload, reference)."""
    pass


@textbooks.command()
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable, JSON values supported).",
)
@click.pass_obj
def add(ctx: dict, filepath: str, meta_pairs: tuple[str, ...]) -> None:
    """Add a textbook to the index."""
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        extra_meta = _parse_meta_pairs(meta_pairs)

        doc = indexer.add_file(filepath, document_type="textbook", extra_metadata=extra_meta or None)
        if doc:
            click.echo(f"Indexed: {doc.path} (type={doc.document_type})")
        else:
            click.echo(f"Failed to index: {filepath}", err=True)
    finally:
        repo.close()


@textbooks.command()
@click.argument("file", type=click.File("rb"))
@click.option("-n", "--name", help="Filename to save as (default: original name).")
@click.option("-D", "--directory", default="", help="Subdirectory within database home to save into.")
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable, JSON values supported).",
)
@click.pass_obj
def upload(ctx: dict, file, name: str | None, directory: str, meta_pairs: tuple[str, ...]) -> None:
    """Upload a textbook and index it automatically."""
    import shutil
    config = ctx["config"]

    target_dir = config.home / directory if directory else config.home
    target_dir = target_dir.resolve()
    if not str(target_dir).startswith(str(config.home)):
        click.echo("Directory must be within the database home.", err=True)
        return

    if not target_dir.is_dir():
        click.echo(f"Directory does not exist: {target_dir}", err=True)
        return

    original_name = Path(file.name).name if hasattr(file, "name") and file.name else "uploaded.pdf"
    filename = name or original_name
    target_path = target_dir / filename

    with open(target_path, "wb") as f:
        shutil.copyfileobj(file, f)

    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        extra_meta = _parse_meta_pairs(meta_pairs)

        rel_target = str(target_path.relative_to(config.home))
        doc = indexer.add_file(rel_target, document_type="textbook", extra_metadata=extra_meta or None)
        if doc:
            click.echo(f"Uploaded & indexed: {doc.path}")
        else:
            click.echo(f"Failed to index uploaded file: {target_path}", err=True)
    finally:
        repo.close()


@click.command(name="reference")
@click.option("-t", "--title", required=True, help="Title of the textbook (required).")
@click.option("-a", "--author", default=None, help="Author string.")
@click.option("-y", "--year", default=None, help="Year of publication.")
@click.option("--publisher", default=None, help="Publisher name.")
@click.option("-e", "--edition", default=None, help="Edition string.")
@click.option("-u", "--url", default=None, help="URL string.")
@click.option(
    "-D", "--path", "filepath", default="",
    help="Path for grouping (file need not exist yet).",
)
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable, JSON values supported).",
)
@click.pass_obj
def reference(ctx: dict, title: str, author: str | None, year: str | None, publisher: str | None,
              edition: str | None, url: str | None, filepath: str, meta_pairs: tuple[str, ...]) -> None:
    """Register a metadata-only textbook reference (no file required).

    Creates an index entry with ``source_type='reference'`` from supplied
    metadata. The ``--path`` option sets a real path for grouping within the
    database home; the file need not exist. If placed at that path later, a
    normal ``textbooks add`` will enrich the entry in-place.
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        extra_meta = _parse_meta_pairs(meta_pairs) or {}
        extra_meta["title"] = title
        if author:
            extra_meta["author"] = author
        if year:
            extra_meta["year"] = year
        if publisher:
            extra_meta["publisher"] = publisher
        if edition:
            extra_meta["edition"] = edition
        if url:
            extra_meta["url"] = url

        doc = indexer.add_reference(filepath, document_type="textbook", extra_metadata=extra_meta or None)
        if doc:
            click.echo(f"Reference registered: {doc.path} (type={doc.document_type})")
        else:
            click.echo("Failed to create reference.", err=True)
    finally:
        repo.close()


# ── helpers ────────────────────────────────────────────────────────

@textbooks.command(name="init")
@click.argument("directory", type=click.Path())
@click.option("-t", "--title", default=None, help="Title of the textbook (default: directory name).")
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable, JSON values supported).",
)
@click.pass_obj
def init(ctx: dict, directory: str, title: str | None, meta_pairs: tuple[str, ...]) -> None:
    """Initialize an empty directory-type textbook.

    Creates an empty directory at the specified path with a Document entry so
    chapters can be associated later via ``textbooks attach-chapter``.
    The directory path may be relative (resolved against the database home)
    or absolute.
    """
    config = ctx["config"]
    root = config.home

    # Resolve directory relative to database home
    dir_p = Path(directory)
    if dir_p.is_absolute():
        dir_p = dir_p.resolve()
    else:
        dir_p = (root / dir_p).resolve()

    # Enforce containment within database home
    if not str(dir_p).startswith(str(root)):
        click.echo("Directory must be within the database home.", err=True)
        return

    extra_meta = _parse_meta_pairs(meta_pairs) or {}
    if title:
        extra_meta["title"] = title

    dir_p.mkdir(parents=True, exist_ok=True)

    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        rel_dir = str(dir_p.relative_to(root))
        doc = indexer.add_file(rel_dir, document_type="textbook", extra_metadata=extra_meta or None)
        if doc:
            click.echo(f"Textbook directory initialized: {doc.path} (type={doc.document_type}, source={doc.source_type})")
        else:
            click.echo("Failed to initialize textbook directory.", err=True)
    finally:
        repo.close()


@textbooks.command(name="attach-chapter")
@click.argument("doc_id", type=int)
@click.argument("chapter_filepath", type=click.Path(exists=True, dir_okay=False))
@click.option("--index", "-i", "chapter_index", default=None, type=int, help="Explicit chapter index (auto-assigned if omitted).")
@click.pass_obj
def attach_chapter(ctx: dict, doc_id: int, chapter_filepath: str, chapter_index: int | None) -> None:
    """Associate a local chapter file with a directory-type textbook.

    Copies the chapter file into the textbook's directory and creates a
    corresponding chapter entry. If a file with the same name already exists,
    it is overwritten and the old chapter row is replaced.
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            click.echo(f"Document {doc_id} not found.", err=True)
            return
        if doc.document_type != "textbook":
            click.echo(f"Not a textbook: type={doc.document_type}", err=True)
            return
        if doc.source_type != "directory":
            click.echo(
                f"Cannot attach chapter: textbook {doc.filename!r} is source_type '{doc.source_type}', not 'directory'. "
                "Chapter attachment is only supported for directory-type textbooks.",
                err=True,
            )
            return

        textbook_dir = config.home / doc.path
        if not textbook_dir.is_dir():
            click.echo(f"Textbook directory does not exist: {textbook_dir}", err=True)
            return

        src_p = Path(chapter_filepath).resolve()
        name = src_p.name
        target_path = textbook_dir / name

        # If a file already exists at destination, remove its old chapter entry
        old_chapter = repo.get_chapter_by_file_path(doc_id, name)
        if old_chapter and target_path.exists():
            repo.delete_chapter_by_id(old_chapter.id)

        # Copy the chapter file (overwrites if exists)
        shutil.copy2(str(src_p), str(target_path))

        # Auto-assign chapter_index if not provided
        if chapter_index is None:
            existing = repo.get_chapters(doc_id)
            used_indices = {ch.chapter_index for ch in existing}
            idx = 0
            while idx in used_indices:
                idx += 1
            chapter_index = idx

        # Extract text and metadata from the chapter file
        extractors = load_extractors()
        ext = src_p.suffix.lower().lstrip(".")
        extractor = extractors.get(ext)

        extracted_meta: dict[str, Any] = {}
        full_text = ""
        page_count: int | None = None

        if extractor:
            extracted_meta, full_text = extractor.extract(str(target_path))

            # Get page count for PDFs
            try:
                import fitz
                with fitz.open(str(target_path)) as pdf_doc:
                    page_count = len(pdf_doc)
            except Exception:
                pass

        title = name.replace(".pdf", "").replace("_", " ").replace("-", " ").title()

        chapter = Chapter(
            textbook_id=doc_id,
            chapter_index=chapter_index,
            title=title,
            chapter_type="file",
            start_page=None,
            end_page=None,
            page_count=page_count,
            file_path=name,
            metadata=extracted_meta,
            full_text=full_text,
        )
        repo.upsert_chapter(chapter)

        click.echo(f"Attached chapter: {chapter_filepath} → index={chapter_index}, title={title}")
    finally:
        repo.close()


@textbooks.command(name="chapters")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.pass_obj
def chapters(ctx: dict, filepath: str) -> None:
    """List all indexed chapters for a textbook."""
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        # Convert user-supplied path to relative for DB lookup
        abs_p = Path(filepath).resolve()
        rel_p = str(abs_p.relative_to(config.home))
        doc = repo.get(rel_p)
        if not doc:
            click.echo(f"Not indexed: {filepath}", err=True)
            return
        if doc.document_type != "textbook":
            click.echo(f"Not a textbook: {filepath} (type={doc.document_type})", err=True)
            return

        chapter_list = repo.get_chapters(doc.id)
        if not chapter_list:
            click.echo("No chapters found.")
            return

        click.echo(f"\n{'Index':<7} {'Title':<40} {'Pages':<15}")
        click.echo("-" * 62)
        for ch in chapter_list:
            pages = f"{ch.start_page}–{ch.end_page}"
            click.echo(f"{ch.chapter_index:<7} {ch.title:<40} {pages:<15}")
        click.echo()
    finally:
        repo.close()


@textbooks.command(name="chapter")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option("--index", "-i", required=True, type=int, help="Chapter index (zero-based).")
@click.pass_obj
def chapter(ctx: dict, filepath: str, index: int) -> None:
    """Print the full text of a specific chapter."""
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        # Convert user-supplied path to relative for DB lookup
        abs_p = Path(filepath).resolve()
        rel_p = str(abs_p.relative_to(config.home))
        doc = repo.get(rel_p)
        if not doc:
            click.echo(f"Not indexed: {filepath}", err=True)
            return
        if doc.document_type != "textbook":
            click.echo(f"Not a textbook: {filepath} (type={doc.document_type})", err=True)
            return

        ch = repo.get_chapter(doc.id, index)
        if not ch:
            click.echo(f"Chapter {index} not found.", err=True)
            return

        click.echo(f"Chapter {ch.chapter_index}: {ch.title} (pp. {ch.start_page}–{ch.end_page})\n")
        click.echo(ch.full_text)
    finally:
        repo.close()


def _parse_meta_pairs(pairs: tuple[str, ...]) -> dict:
    """Parse ``-m KEY=VALUE`` pairs into a dict."""
    meta: dict = {}
    for pair in pairs:
        if "=" not in pair:
            click.echo(f"Invalid metadata pair: {pair} (expected KEY=VALUE)", err=True)
            continue
        key, value = pair.split("=", 1)
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass
        meta[key] = value
    return meta if meta else None
