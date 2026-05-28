from __future__ import annotations

import json

import click

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository


@click.command(name="reference")
@click.option("-t", "--title", required=True, help="Title of the reference (required).")
@click.option("-a", "--author", default=None, help="Author string.")
@click.option("-s", "--subject", default=None, help="Subject/description.")
@click.option("-k", "--keywords", default=None, help="Comma-separated keywords.")
@click.option("-u", "--url", default=None, help="URL string.")
@click.option(
    "-p", "--path", "filepath", default="",
    help="Path for grouping (file need not exist yet).",
)
@click.option(
    "-T", "--document-type", "document_type", default="generic",
    type=click.Choice(["generic", "paper", "textbook"]),
    help="Document type (default: generic).",
)
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable, JSON values supported).",
)
@click.pass_obj
def reference(
    ctx: dict,
    title: str,
    author: str | None,
    subject: str | None,
    keywords: str | None,
    url: str | None,
    filepath: str,
    document_type: str,
    meta_pairs: tuple[str, ...],
) -> None:
    """Register a metadata-only reference (no file required).

    Creates an index entry with ``source_type='reference'`` from supplied
    metadata. The ``--path`` option sets a real path for grouping within the
    database home; the file need not exist. If placed at that path later, a
    normal ``index add`` will enrich the entry in-place.

    Use ``papers reference`` or ``textbooks reference`` for type-specific
    metadata fields (DOI, journal, publisher, etc.).
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        indexer = Indexer(repo, config.home)
        extra_meta = _parse_meta_pairs(meta_pairs) or {}
        extra_meta["title"] = title
        if author:
            extra_meta["author"] = author
        if subject:
            extra_meta["subject"] = subject
        if keywords:
            extra_meta["keywords"] = [kw.strip() for kw in keywords.split(",")]
        if url:
            extra_meta["url"] = url

        doc = indexer.add_reference(filepath, document_type=document_type, extra_metadata=extra_meta or None)
        if doc:
            click.echo(f"Reference registered: {doc.path} (type={doc.document_type})")
        else:
            click.echo("Failed to create reference.", err=True)
    finally:
        repo.close()


def _parse_meta_pairs(pairs: tuple[str, ...]) -> dict | None:
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
