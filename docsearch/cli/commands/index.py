from __future__ import annotations

import json
from pathlib import Path

import click

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository


@click.group(name="index")
def index() -> None:
    """Index management: scan directories, add or remove files."""
    pass


@index.command()
@click.argument("dirpath", type=click.Path(exists=True, file_okay=False))
@click.option("--no-recursive", is_flag=True, help="Only scan the top-level directory.")
@click.option(
    "--type", "document_type",
    default="generic",
    type=click.Choice(["generic", "paper", "textbook"], case_sensitive=False),
    show_default=True,
    help="Document type for indexing.",
)
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable, JSON values supported).",
)
@click.option("--skip-bib", is_flag=True, help="Skip pdf2bib processing (papers only).")
@click.pass_obj
def scan(ctx: dict, dirpath: str, no_recursive: bool, document_type: str, meta_pairs: tuple[str, ...], skip_bib: bool) -> None:
    """Scan a directory tree and sync the index."""
    config = ctx["config"]
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        extra_meta = _parse_meta_pairs(meta_pairs)
        stats = indexer.scan_directory(dirpath, recursive=not no_recursive, document_type=document_type, extra_metadata=extra_meta or None, skip_bib=skip_bib)
        click.echo(f"Scanned: {dirpath}")
        click.echo(f"  Added:     {stats['added']}")
        click.echo(f"  Updated:   {stats['updated']}")
        click.echo(f"  Removed:   {stats['removed']}")
        click.echo(f"  Skipped:   {stats['skipped']}")
        click.echo(f"  Errors:    {stats['errors']}")
    finally:
        repo.close()


@index.command()
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--type", "document_type",
    default="generic",
    type=click.Choice(["generic", "paper", "textbook"], case_sensitive=False),
    show_default=True,
    help="Document type for indexing.",
)
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable, JSON values supported).",
)
@click.option("--skip-bib", is_flag=True, help="Skip pdf2bib processing (papers only).")
@click.pass_obj
def add(ctx: dict, filepath: str, document_type: str, meta_pairs: tuple[str, ...], skip_bib: bool) -> None:
    """Add a single file to the index."""
    config = ctx["config"]
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        extra_meta = _parse_meta_pairs(meta_pairs)
        doc = indexer.add_file(filepath, document_type=document_type, extra_metadata=extra_meta or None, skip_bib=skip_bib)
        if doc:
            click.echo(f"Indexed: {doc.path} (type={doc.document_type})")
        else:
            click.echo(f"Failed to index: {filepath}", err=True)
    finally:
        repo.close()


@index.command()
@click.argument("filepath", type=click.Path())
@click.pass_obj
def remove(ctx: dict, filepath: str) -> None:
    """Remove a file from the index."""
    config = ctx["config"]
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        if indexer.remove_file(filepath):
            click.echo(f"Removed: {filepath}")
        else:
            click.echo(f"Not found in index: {filepath}", err=True)
    finally:
        repo.close()


@index.command()
@click.argument("filepath", type=click.Path(exists=True))
@click.pass_obj
def status(ctx: dict, filepath: str) -> None:
    """Check whether a file needs re-indexing."""
    config = ctx["config"]
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        needs = indexer.needs_reindex(filepath)
        if needs:
            click.echo(f"{filepath} → needs indexing")
        else:
            click.echo(f"{filepath} → up to date")
    finally:
        repo.close()


# ── helpers ────────────────────────────────────────────────────────

def _parse_meta_pairs(pairs: tuple[str, ...]) -> dict:
    """Parse ``-m KEY=VALUE`` pairs into a dict.

    Values are parsed as JSON when possible; otherwise kept as plain strings.
    """
    meta: dict = {}
    for pair in pairs:
        if "=" not in pair:
            click.echo(f"Invalid metadata pair: {pair} (expected KEY=VALUE)", err=True)
            continue
        key, value = pair.split("=", 1)
        # Try JSON parse first, fall back to plain string
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass
        meta[key] = value
    return meta
