from __future__ import annotations

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
@click.pass_obj
def scan(ctx: dict, dirpath: str, no_recursive: bool) -> None:
    """Scan a directory tree and sync the index."""
    config = ctx["config"]
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        stats = indexer.scan_directory(dirpath, recursive=not no_recursive)
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
    """Add a single file to the index."""
    config = ctx["config"]
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        doc = indexer.add_file(filepath)
        if doc:
            click.echo(f"Indexed: {doc.path}")
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
