from __future__ import annotations

import json
from pathlib import Path

import click

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository


@click.group(name="papers")
def papers() -> None:
    """Manage research papers (add, upload, export bibtex)."""
    pass


@papers.command()
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option("-d", "--doi", help="DOI to embed into the PDF before bibliographic extraction.")
@click.option("--skip-bib", is_flag=True, help="Skip pdf2bib processing (generate bibtex from available metadata only).")
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable, JSON values supported).",
)
@click.pass_obj
def add(ctx: dict, filepath: str, doi: str | None, skip_bib: bool, meta_pairs: tuple[str, ...]) -> None:
    """Add a research paper to the index.

    If ``--doi`` is provided it will be embedded into the PDF before running
    pdf2bib, ensuring correct bibliographic resolution.  Use ``--skip-bib`` to
    bypass pdf2bib entirely.
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo)
        extra_meta = _parse_meta_pairs(meta_pairs)
        if doi:
            extra_meta["doi"] = doi

        doc = indexer.add_file(filepath, document_type="paper", extra_metadata=extra_meta or None, skip_bib=skip_bib)
        if doc:
            click.echo(f"Indexed: {doc.path} (type={doc.document_type})")
        else:
            click.echo(f"Failed to index: {filepath}", err=True)
    finally:
        repo.close()


@papers.command()
@click.argument("file", type=click.File("rb"))
@click.option("-n", "--name", help="Filename to save as (default: original name).")
@click.option("-D", "--directory", default="", help="Subdirectory within database home to save into.")
@click.option("-d", "--doi", help="DOI to embed into the PDF before bibliographic extraction.")
@click.option("--skip-bib", is_flag=True, help="Skip pdf2bib processing.")
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable, JSON values supported).",
)
@click.pass_obj
def upload(ctx: dict, file, name: str | None, directory: str, doi: str | None, skip_bib: bool, meta_pairs: tuple[str, ...]) -> None:
    """Upload a research paper and index it automatically."""
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
        if doi:
            extra_meta["doi"] = doi

        doc = indexer.add_file(str(target_path), document_type="paper", extra_metadata=extra_meta or None, skip_bib=skip_bib)
        if doc:
            click.echo(f"Uploaded & indexed: {doc.path}")
        else:
            click.echo(f"Failed to index uploaded file: {target_path}", err=True)
    finally:
        repo.close()


# ── helpers ────────────────────────────────────────────────────────

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
