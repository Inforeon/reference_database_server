from __future__ import annotations

import json
from pathlib import Path

import click

from docsearch.cli.utils import parse_meta_value, resolve_user_path_to_home_relative
from docsearch.config import Config
from docsearch.core.indexer import Indexer
from docsearch.core.models import Document
from docsearch.core.repository import Repository
from docsearch.core.sidecars import load_sidecar, sidecar_path


@click.group(name="meta")
def meta() -> None:
    """Manage a document's metadata."""
    pass


@meta.command(name="show")
@click.argument("filepath")
@click.pass_obj
def meta_show(ctx: dict, filepath: str) -> None:
    """Display the metadata for a file."""
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = _lookup(repo, config, filepath)
        if doc is not None:
            click.echo(json.dumps(doc.sidecar_metadata, indent=2))
            return

        # Not indexed — fall back to the file so hand-written sidecars are readable.
        data = load_sidecar(_find_sidecar(filepath))
        if data:
            click.echo(json.dumps(data, indent=2))
        else:
            click.echo(f"No sidecar metadata for {filepath}")
    finally:
        repo.close()


@meta.command(name="set")
@click.argument("filepath")
@click.option("-k", "--key", required=True, help="Metadata key.")
@click.option(
    "-v", "--value", required=True,
    help="Metadata value. Parsed as JSON when possible (numbers, lists, objects); "
         "quote it to keep a string, e.g. -v '\"1706.03762\"'.",
)
@click.pass_obj
def meta_set(ctx: dict, filepath: str, key: str, value: str) -> None:
    """Set a metadata key on an indexed document.

    Updates the index and the sidecar file together, without re-extracting the
    document.  Reference-only entries are supported even though they have no
    file on disk.
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc, doc_id = _require_indexed(repo, config, filepath)
        indexer = Indexer(repo, config.home)
        parsed = parse_meta_value(value)
        indexer.set_metadata_key(doc_id, key, parsed)
        shown = json.dumps(parsed)
        click.echo(f"Set '{key}' = {shown} on {doc.path}")
    finally:
        repo.close()


@meta.command(name="delete")
@click.argument("filepath")
@click.option("-k", "--key", required=True, help="Metadata key to remove.")
@click.pass_obj
def meta_delete(ctx: dict, filepath: str, key: str) -> None:
    """Remove a metadata key from an indexed document."""
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc, doc_id = _require_indexed(repo, config, filepath)
        indexer = Indexer(repo, config.home)
        present = key in doc.sidecar_metadata or key in load_sidecar(
            indexer.metadata_sidecar_path(doc)
        )
        indexer.delete_metadata_key(doc_id, key)
        if present:
            click.echo(f"Removed key '{key}' from {doc.path}")
        else:
            click.echo(f"Key '{key}' not set on {doc.path}", err=True)
    finally:
        repo.close()


@meta.command(name="init")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
def meta_init(filepath: str) -> None:
    """Create an empty sidecar metadata file."""
    sidecar = _find_sidecar(filepath)
    with open(sidecar, "w") as f:
        json.dump({}, f)
    click.echo(f"Created: {sidecar}")


def _lookup(repo: Repository, config: Config, filepath: str) -> Document | None:
    """Resolve a user-supplied path to an indexed document, or None."""
    try:
        rel = resolve_user_path_to_home_relative(config, filepath)
    except click.ClickException:
        return None
    return repo.get(rel)


def _require_indexed(repo: Repository, config: Config, filepath: str) -> tuple[Document, int]:
    """Return an indexed document with its id, or raise a user-facing error.

    The id travels out as a separate value so callers never handle the
    ``Optional`` — every row read back from the index has one, but the type says
    otherwise and that shouldn't be pushed onto each command.
    """
    doc = _lookup(repo, config, filepath)
    if doc is None or doc.id is None:
        raise click.ClickException(
            f"'{filepath}' is not an indexed document. Metadata edits apply to "
            f"indexed entries — add it first, or check the path."
        )
    return doc, doc.id


def _find_sidecar(filepath: str) -> Path:
    return sidecar_path(Path(filepath).resolve())
