from __future__ import annotations

import json
from pathlib import Path

import click

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository


@click.group(name="textbooks")
def textbooks() -> None:
    """Manage textbooks (add, upload)."""
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
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
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

    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        extra_meta = _parse_meta_pairs(meta_pairs)

        doc = indexer.add_file(str(target_path), document_type="textbook", extra_metadata=extra_meta or None)
        if doc:
            click.echo(f"Uploaded & indexed: {doc.path}")
        else:
            click.echo(f"Failed to index uploaded file: {target_path}", err=True)
    finally:
        repo.close()


# ── helpers ────────────────────────────────────────────────────────

@textbooks.command(name="chapters")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.pass_obj
def chapters(ctx: dict, filepath: str) -> None:
    """List all indexed chapters for a textbook."""
    config = ctx["config"]
    repo = Repository(str(config.db_path))
    try:
        doc = repo.get(filepath)
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
    repo = Repository(str(config.db_path))
    try:
        doc = repo.get(filepath)
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
