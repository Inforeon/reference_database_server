from __future__ import annotations

import json
from pathlib import Path

import click

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository


@click.group(name="index")
def index() -> None:
    """Index management: scan directories, add or remove files.

    For document-type-specific behaviour (e.g. paper DOI resolution), prefer
    the dedicated ``papers`` and ``textbooks`` command groups.
    """
    pass


@index.command()
@click.argument("dirpath", type=click.Path(exists=True, file_okay=False))
@click.option("--no-recursive", is_flag=True, help="Only scan the top-level directory.")
@click.pass_obj
def scan(ctx: dict, dirpath: str, no_recursive: bool) -> None:
    """Scan a directory tree and sync the index (generic documents only).

    For document-type-specific scanning use ``papers`` or ``textbooks`` commands.
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        stats = indexer.scan_directory(dirpath, recursive=not no_recursive, document_type="generic")
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
@click.pass_obj
def add(ctx: dict, filepath: str) -> None:
    """Add a single generic document to the index.

    For document-type-specific behaviour use ``papers add`` or ``textbooks add``.
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        doc = indexer.add_file(filepath, document_type="generic")
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
