from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import click

from docsearch.cli.utils import parse_breakpoints, parse_meta_pairs, resolve_user_path_to_home_relative
from docsearch.core.indexer import Indexer
from docsearch.core.models import Chapter
from docsearch.core.repository import Repository
from docsearch.core import slicing
from docsearch.extractors import load_extractors


@click.group(name="textbooks")
def textbooks() -> None:
    """Manage textbooks (add, reference, chapters)."""
    pass


@textbooks.command()
@click.argument("filepath")
@click.option("-n", "--name", help="Filename to save as when copying (default: original name).")
@click.option("-D", "--directory", default="", help="Subdirectory within database home to copy into before indexing.")
@click.option(
    "-b", "--breakpoints", default=None,
    help="Chapter breakpoints as JSON. List [5,10,15] for page boundaries "
         "(auto-named chapters), or dict {\"Intro\":5,\"Methods\":null} for named chapters.",
)
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable). Values are parsed as JSON "
         "when possible; quote to keep a string: -m isbn='\"0000000000\"'.",
)
@click.pass_obj
def add(ctx: dict, filepath: str, name: str | None, directory: str,
        breakpoints: str | None, meta_pairs: tuple[str, ...]) -> None:
    """Add a textbook to the index.

    If ``--directory`` or ``--name`` is given, the file is copied into the
    database home before indexing.  Otherwise the file is indexed in place.
    """
    config = ctx["config"]
    extra_meta = parse_meta_pairs(meta_pairs) or {}

    if breakpoints is not None:
        if "chapters" in extra_meta:
            click.echo(
                "Warning: both --breakpoints and -m chapters=... provided; "
                "--breakpoints takes precedence, ignoring -m chapters.",
                err=True,
            )
        extra_meta["chapters"] = parse_breakpoints(breakpoints)

    # Determine whether to copy or index in place
    if directory or name:
        # Copy file into database home
        src_path = Path(filepath).resolve()
        if not src_path.is_file():
            raise click.ClickException(f"Source file does not exist: {filepath}")

        target_dir = config.home / directory if directory else config.home
        target_dir = target_dir.resolve()
        if not str(target_dir).startswith(str(config.home)):
            raise click.ClickException("Directory must be within the database home.")
        if not target_dir.is_dir():
            raise click.ClickException(f"Directory does not exist: {target_dir}")

        filename = name or src_path.name
        target_path = target_dir / filename

        shutil.copy2(str(src_path), str(target_path))
        rel_filepath = str(target_path.relative_to(config.home))
    else:
        # Index in place
        rel_filepath = resolve_user_path_to_home_relative(config, filepath, require_file=True)

    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        doc = indexer.add_file(rel_filepath, document_type="textbook", extra_metadata=extra_meta or None)
        if doc:
            click.echo(f"Indexed: {doc.path} (type={doc.document_type})")
        else:
            click.echo(f"Failed to index: {filepath}", err=True)
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
    help="Extra metadata as KEY=VALUE (repeatable). Values are parsed as JSON "
         "when possible; quote to keep a string: -m isbn='\"0000000000\"'.",
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
        extra_meta = parse_meta_pairs(meta_pairs) or {}
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
    help="Extra metadata as KEY=VALUE (repeatable). Values are parsed as JSON "
         "when possible; quote to keep a string: -m isbn='\"0000000000\"'.",
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

    extra_meta = parse_meta_pairs(meta_pairs) or {}
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
@click.argument("filepath")
@click.pass_obj
def chapters(ctx: dict, filepath: str) -> None:
    """List all indexed chapters for a textbook."""
    config = ctx["config"]
    rel_filepath = resolve_user_path_to_home_relative(config, filepath, require_file=True)
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get(rel_filepath)
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
@click.argument("filepath")
@click.option("--index", "-i", required=True, type=int, help="Chapter index (zero-based).")
@click.option("-s", "--sections", help="Comma-separated section indices to retrieve.")
@click.option("--lines", help="Comma-separated line ranges (e.g. '0-99,200-299').")
@click.option("--list-sections", "-L", is_flag=True, help="List sections for this chapter.")
@click.option("--set-section", type=(str, int, int), nargs=3, metavar="NAME START END", help="Add a section (NAME START_LINE END_LINE).")
@click.option("--delete-section", "-D", type=int, metavar="INDEX", help="Delete a section by index.")
@click.pass_obj
def chapter(ctx: dict, filepath: str, index: int, sections: str | None, lines: str | None,
            list_sections: bool, set_section: tuple[str, int, int] | None,
            delete_section: int | None) -> None:
    """Print the full text of a specific chapter.

    Optionally slice by section indices (``--sections``) or line ranges
    (``--lines``). Manage sections with ``--list-sections``, ``--set-section``,
    and ``--delete-section``.
    """
    config = ctx["config"]
    rel_filepath = resolve_user_path_to_home_relative(config, filepath, require_file=True)
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get(rel_filepath)
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

        # Section management operations
        if list_sections:
            sec_list = slicing.get_sections_map(ch.metadata)
            if not sec_list:
                click.echo(f"No sections defined on chapter '{ch.title}'")
                return
            lines_all = slicing.split_lines(ch.full_text)
            click.echo(f"Sections for chapter [{index}] {ch.title}:")
            for sec in sec_list:
                end_label = sec["end"] if sec["end"] is not None else "end"
                count = (sec["end"] if sec["end"] is not None else len(lines_all)) - sec["start"] + 1
                click.echo(f"  [{sec['index']}] {sec['name']} (lines {sec['start']}–{end_label}, {count} lines)")
            return

        if set_section:
            name, start, end = set_section
            # Get current metadata and add section
            meta = dict(ch.metadata)
            current_sections = meta.get("sections", {})
            existing_indices = [int(k) for k in current_sections.keys() if k.isdigit()]
            new_idx = max(existing_indices, default=-1) + 1
            current_sections[str(new_idx)] = {"name": name, "start": start, "end": end}
            meta["sections"] = current_sections
            # Update chapter metadata in DB
            from docsearch.core.models import Chapter as ChapterModel
            ch.metadata = meta
            repo.upsert_chapter(ch)
            click.echo(f"Added section '{name}' at index {new_idx}")
            return

        if delete_section is not None:
            meta = dict(ch.metadata)
            sections_dict = meta.get("sections", {})
            if str(delete_section) not in sections_dict:
                available = list(sections_dict.keys()) if sections_dict else "(none)"
                click.echo(f"Section {delete_section} not found. Available: {available}", err=True)
                return
            del sections_dict[str(delete_section)]
            reindexed = slicing.reindex_sections(sections_dict)
            if reindexed:
                meta["sections"] = reindexed
            else:
                meta.pop("sections", None)
            ch.metadata = meta
            repo.upsert_chapter(ch)
            click.echo(f"Deleted section {delete_section}")
            return

        # Text retrieval
        lines_all = slicing.split_lines(ch.full_text)

        if sections:
            parts = []
            for idx_str in sections.split(","):
                idx = int(idx_str.strip())
                sec_list = slicing.get_sections_map(ch.metadata)
                sec = next((s for s in sec_list if s["index"] == idx), None)
                if not sec:
                    click.echo(f"Section {idx} not found", err=True)
                    return
                text = slicing.get_section_text(lines_all, sec)
                parts.append(text)
            click.echo("\n".join(parts))
        elif lines:
            text = slicing.slice_lines(lines_all, lines)
            click.echo(text)
        else:
            click.echo(f"Chapter {ch.chapter_index}: {ch.title} (pp. {ch.start_page}–{ch.end_page})\n")
            click.echo(ch.full_text)
    finally:
        repo.close()


@textbooks.command(name="set-chapters")
@click.argument("filepath")
@click.option(
    "-b", "--breakpoints", required=True,
    help="Chapter breakpoints as JSON. List [5,10,15] for page boundaries "
         "(auto-named chapters), or dict {\"Intro\":5,\"Methods\":null} for named chapters.",
)
@click.pass_obj
def set_chapters(ctx: dict, filepath: str, breakpoints: str) -> None:
    """Redefine chapter breakpoints for a file-type textbook.

    Deletes existing chapters and re-extracts from the PDF using new breakpoints.
    """
    config = ctx["config"]
    rel_filepath = resolve_user_path_to_home_relative(config, filepath, require_file=True)
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get(rel_filepath)
        if not doc:
            click.echo(f"Not indexed: {filepath}", err=True)
            return
        if doc.document_type != "textbook":
            click.echo(f"Not a textbook: {filepath} (type={doc.document_type})", err=True)
            return
        if doc.source_type == "directory":
            click.echo(
                f"Cannot set breakpoints on directory-type textbook '{doc.path}'. "
                "Use attach-chapter/detach-chapter for directory-type textbooks.",
                err=True,
            )
            return

        abs_path = config.home / doc.path
        if not abs_path.is_file():
            click.echo(f"Textbook file not found on disk: {abs_path}", err=True)
            return

        # Parse breakpoints into chapters list
        chapters_list = parse_breakpoints(breakpoints)

        # Use the handler to detect and insert chapters with proper page resolution
        from docsearch.core.handlers import TextbookDocumentHandler
        from docsearch.core.indexer import Indexer

        handler = TextbookDocumentHandler(repo, config.home)
        detected = handler._detect_chapters(abs_path, {"chapters": chapters_list})

        # Delete existing chapters and insert new ones
        repo.delete_chapters(doc.id)
        handler._insert_file_chapters(abs_path, doc.id, detected)

        # Update sidecar with new breakpoints
        indexer = Indexer(repo, config.home)
        indexer.set_metadata_key(doc.id, "chapters", chapters_list)

        click.echo(f"Updated {len(detected)} chapters for {doc.path}")
    finally:
        repo.close()


@textbooks.command(name="detach-chapter")
@click.argument("doc_id", type=int)
@click.argument("chapter_index", type=int)
@click.pass_obj
def detach_chapter(ctx: dict, doc_id: int, chapter_index: int) -> None:
    """Remove a chapter from a directory-type textbook.

    Deletes the database row and the physical file.
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
                f"Cannot detach chapter: textbook {doc.filename!r} is source_type '{doc.source_type}', not 'directory'. "
                "Chapter detachment is only supported for directory-type textbooks.",
                err=True,
            )
            return

        chapter = repo.get_chapter(doc_id, chapter_index)
        if not chapter:
            click.echo(f"Chapter {chapter_index} not found.", err=True)
            return

        # Delete physical file if it exists
        if chapter.file_path:
            textbook_dir = config.home / doc.path
            file_path = textbook_dir / chapter.file_path
            if file_path.is_file():
                file_path.unlink()

        # Delete from database
        repo.delete_chapter_by_id(chapter.id)

        click.echo(f"Detached chapter {chapter_index}: {chapter.title}")
    finally:
        repo.close()

