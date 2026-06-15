from __future__ import annotations

import json
from pathlib import Path

import click

from docsearch.cli.utils import resolve_user_path_to_home_relative
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
@click.argument("dirpath")
@click.option("--no-recursive", is_flag=True, help="Only scan the top-level directory.")
@click.option(
    "-T", "--document-type", "document_type", default="generic",
    type=click.Choice(["generic", "paper", "textbook"]),
    help="Document type for indexing (default: generic).",
)
@click.pass_obj
def scan(ctx: dict, dirpath: str, no_recursive: bool, document_type: str) -> None:
    """Scan a directory tree and sync the index.

    All discovered files are indexed with the given ``--document-type``
    (defaults to ``generic``). For type-specific behaviour use ``papers``
    or ``textbooks`` commands.
    """
    config = ctx["config"]
    rel_dirpath = resolve_user_path_to_home_relative(config, dirpath, require_dir=True)
    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        stats = indexer.scan_directory(rel_dirpath, recursive=not no_recursive, document_type=document_type)
        click.echo(f"Scanned: {dirpath}")
        click.echo(f"  Added:     {stats['added']}")
        click.echo(f"  Updated:   {stats['updated']}")
        click.echo(f"  Removed:   {stats['removed']}")
        click.echo(f"  Skipped:   {stats['skipped']}")
        click.echo(f"  Errors:    {stats['errors']}")
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    finally:
        repo.close()


@index.command()
@click.argument("filepath")
@click.pass_obj
def add(ctx: dict, filepath: str) -> None:
    """Add a single generic document to the index.

    For document-type-specific behaviour use ``papers add`` or ``textbooks add``.
    """
    config = ctx["config"]
    rel_filepath = resolve_user_path_to_home_relative(config, filepath, require_file=True)
    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        doc = indexer.add_file(rel_filepath, document_type="generic")
        if doc:
            click.echo(f"Indexed: {doc.path} (type={doc.document_type})")
        else:
            click.echo(f"Failed to index: {filepath}", err=True)
    finally:
        repo.close()


@index.command()
@click.argument("filepath")
@click.pass_obj
def remove(ctx: dict, filepath: str) -> None:
    """Remove a file from the index."""
    config = ctx["config"]
    rel_filepath = resolve_user_path_to_home_relative(config, filepath)
    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        if indexer.remove_file(rel_filepath):
            click.echo(f"Removed: {filepath}")
        else:
            click.echo(f"Not found in index: {filepath}", err=True)
    finally:
        repo.close()


@index.command()
@click.argument("source")
@click.argument("destination")
@click.pass_obj
def move(ctx: dict, source: str, destination: str) -> None:
    """Move an indexed file to a new location within the database home.

    Paths may be relative (resolved against current working directory) or
    absolute.  If the destination is an existing directory, the file is moved
    into that directory keeping its original name.  Parent directories are
    created automatically when the destination is a new file path.
    The file must already be indexed; the internal ID is preserved.
    """
    config = ctx["config"]
    source_rel = resolve_user_path_to_home_relative(config, source, require_file=True)

    # Resolve destination and handle directory vs file distinction
    dest_abs = Path(destination)
    if dest_abs.is_absolute():
        dest_resolved = dest_abs.resolve()
    else:
        dest_resolved = (Path.cwd() / dest_abs).resolve()

    # If destination is an existing directory, append the source filename
    if dest_resolved.is_dir():
        dest_file = dest_resolved / Path(source_rel).name
    else:
        dest_file = dest_resolved

    # Validate the final destination file path is within home
    try:
        dest_rel = str(dest_file.relative_to(config.home.resolve()))
    except ValueError:
        raise click.ClickException(
            f"Destination '{destination}' resolves to '{dest_file}', which is "
            f"outside the database home ('{config.home}')."
        )

    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        new_doc = indexer.move_file(source_rel, dest_rel)
        if new_doc:
            click.echo(f"Moved: {source} → {new_doc.path}")
        else:
            click.echo(f"Not found in index: {source}", err=True)
    finally:
        repo.close()


@index.command()
@click.argument("filepath")
@click.pass_obj
def status(ctx: dict, filepath: str) -> None:
    """Check whether a file needs re-indexing."""
    config = ctx["config"]
    rel_filepath = resolve_user_path_to_home_relative(config, filepath, require_exists=True)
    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        needs = indexer.needs_reindex(rel_filepath)
        if needs:
            click.echo(f"{filepath} → needs indexing")
        else:
            click.echo(f"{filepath} → up to date")
    finally:
        repo.close()
