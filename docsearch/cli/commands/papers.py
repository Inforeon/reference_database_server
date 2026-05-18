from __future__ import annotations

import json
from pathlib import Path

import click

from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository


@click.group(name="papers")
def papers() -> None:
    """Manage research papers (add, upload, reference, export bibtex)."""
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
        indexer = Indexer(repo, config.home)
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
        indexer = Indexer(repo, config.home)
        extra_meta = _parse_meta_pairs(meta_pairs)
        if doi:
            extra_meta["doi"] = doi

        rel_target = str(target_path.relative_to(config.home))
        doc = indexer.add_file(rel_target, document_type="paper", extra_metadata=extra_meta or None, skip_bib=skip_bib)
        if doc:
            click.echo(f"Uploaded & indexed: {doc.path}")
        else:
            click.echo(f"Failed to index uploaded file: {target_path}", err=True)
    finally:
        repo.close()


@click.command(name="reference")
@click.option("-t", "--title", required=True, help="Title of the reference (required).")
@click.option("-a", "--author", default=None, help="Author string.")
@click.option("-y", "--year", default=None, help="Year of publication.")
@click.option("-j", "--journal", default=None, help="Journal name.")
@click.option("-b", "--booktitle", default=None, help="Book/proceedings name.")
@click.option("-d", "--doi", default=None, help="DOI string.")
@click.option("-u", "--url", default=None, help="URL string.")
@click.option("-k", "--citation-key", default=None, help="BibTeX citation key.")
@click.option(
    "-p", "--path", "filepath", default="",
    help="Path for grouping (file need not exist yet).",
)
@click.option(
    "-m", "--meta", "meta_pairs",
    multiple=True,
    help="Extra metadata as KEY=VALUE (repeatable, JSON values supported).",
)
@click.pass_obj
def reference(ctx: dict, title: str, author: str | None, year: str | None, journal: str | None,
              booktitle: str | None, doi: str | None, url: str | None, citation_key: str | None,
              filepath: str, meta_pairs: tuple[str, ...]) -> None:
    """Register a metadata-only paper reference (no file required).

    Creates an index entry with ``source_type='reference'`` from supplied
    metadata. BibTeX is auto-generated if not provided via ``-m bibtex=...``.

    The ``--path`` option sets a real path for grouping within the database
    home; the file need not exist. If placed at that path later, a normal
    ``papers add`` will enrich the entry in-place.
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path))
    try:
        indexer = Indexer(repo, config.home)
        extra_meta = _parse_meta_pairs(meta_pairs) or {}
        extra_meta["title"] = title
        if author:
            extra_meta["author"] = author
        if year:
            extra_meta["year"] = year
        if journal:
            extra_meta["journal"] = journal
        if booktitle:
            extra_meta["booktitle"] = booktitle
        if doi:
            extra_meta["doi"] = doi
        if url:
            extra_meta["url"] = url
        if citation_key:
            extra_meta["citation_key"] = citation_key

        doc = indexer.add_reference(filepath, document_type="paper", extra_metadata=extra_meta or None)
        if doc:
            click.echo(f"Reference registered: {doc.path} (type={doc.document_type})")
        else:
            click.echo("Failed to create reference.", err=True)
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


# Register the reference command with the papers group (defined above via @click.command)
papers.add_command(reference)
