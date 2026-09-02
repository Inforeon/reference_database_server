from __future__ import annotations

import json

import click

from docsearch.cli.utils import (
    describe_candidates,
    document_path_candidates,
    parse_meta_value,
    relative_to_home,
)
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
@click.option("-k", "--key", default="", help="Show one key instead of the whole record.")
@click.pass_obj
def meta_show(ctx: dict, filepath: str, key: str) -> None:
    """Display the metadata for a file.

    Reads the indexed record when there is one, otherwise the sidecar file on
    disk, so hand-written metadata is readable before the file is added.
    A path that matches neither is an error rather than empty output — silent
    nothing is indistinguishable from "this document has no metadata".
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = _lookup(repo, config, filepath)
        if doc is not None:
            _emit(doc.sidecar_metadata, key, doc.path)
            return

        found = _sidecar_on_disk(config, filepath)
        if found is not None:
            data, sidecar = found
            _emit(data, key, str(sidecar))
            return

        raise click.ClickException(
            f"{describe_candidates(filepath, document_path_candidates(config, filepath))} "
            "is neither an indexed document nor a file with sidecar metadata."
        )
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
@click.argument("filepath")
@click.pass_obj
def meta_init(ctx: dict, filepath: str) -> None:
    """Create an empty sidecar metadata file.

    An existing sidecar is left alone — this command should never be the way a
    populated record gets wiped.
    """
    config = ctx["config"]
    candidates = document_path_candidates(config, filepath)
    target = next((c for c in candidates if c.is_file()), None)
    if target is None:
        raise click.ClickException(
            f"No such file: {describe_candidates(filepath, candidates)}"
        )
    if relative_to_home(config, target) is None:
        raise click.ClickException(
            f"'{target}' is outside the database home ('{config.home.resolve()}'). "
            "Sidecars are only managed for files inside the home."
        )

    sidecar = sidecar_path(target)
    if sidecar.exists():
        click.echo(f"Already exists: {sidecar}")
        return
    with open(sidecar, "w") as f:
        json.dump({}, f)
    click.echo(f"Created: {sidecar}")


def _lookup(repo: Repository, config: Config, filepath: str) -> Document | None:
    """Resolve a user-supplied path to an indexed document, or None.

    Every candidate location is tried rather than just the cwd-relative one, so
    a database-root-relative path works from any directory.  Resolution failures
    stay quiet here — the caller turns "nothing matched" into an error that can
    name everything it looked for.
    """
    for candidate in document_path_candidates(config, filepath):
        rel = relative_to_home(config, candidate)
        if rel is None:
            continue
        doc = repo.get(rel)
        if doc is not None:
            return doc
    return None


def _sidecar_on_disk(config: Config, filepath: str) -> tuple[dict, Path] | None:
    """Load the first sidecar file belonging to one of the candidate paths."""
    for candidate in document_path_candidates(config, filepath):
        sidecar = sidecar_path(candidate)
        if sidecar.is_file():
            return load_sidecar(sidecar), sidecar
    return None


def _require_indexed(repo: Repository, config: Config, filepath: str) -> tuple[Document, int]:
    """Return an indexed document with its id, or raise a user-facing error.

    The id travels out as a separate value so callers never handle the
    ``Optional`` — every row read back from the index has one, but the type says
    otherwise and that shouldn't be pushed onto each command.
    """
    doc = _lookup(repo, config, filepath)
    if doc is None or doc.id is None:
        raise click.ClickException(
            f"{describe_candidates(filepath, document_path_candidates(config, filepath))} "
            "is not an indexed document. Metadata edits apply to indexed entries — "
            "add it first, or check the path."
        )
    return doc, doc.id


def _emit(data: dict, key: str, label: str) -> None:
    """Print a metadata record, or just one key of it.

    ``-k`` exists because an author-heavy paper record scrolls the interesting
    fields off the terminal; strings print bare so they can be piped straight
    into another command.
    """
    if not key:
        click.echo(json.dumps(data, indent=2))
        return
    if key not in data:
        available = ", ".join(sorted(data)) or "(none set)"
        raise click.ClickException(f"'{key}' is not set on {label}. Available keys: {available}")
    value = data[key]
    click.echo(value if isinstance(value, str) else json.dumps(value, indent=2))
