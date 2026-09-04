from __future__ import annotations

import click

from docsearch.config import Config


@click.group()
@click.option("--home", type=click.Path(), default=None, help="Database home directory (default: current working directory).")
@click.pass_context
def cli(ctx: click.Context, home: str | None) -> None:
    """docsearch — Document metadata index and search engine."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config(home=home)


@cli.command()
@click.argument("doc_id", type=int, required=False)
@click.option(
    "--verbose", is_flag=True, default=False,
    help="Include full metadata when showing document info (default: compact).",
)
@click.pass_context
def info(ctx: click.Context, doc_id: int | None, verbose: bool) -> None:
    """Show database location and index statistics.

    With an optional DOC_ID argument, display full metadata for that document.
    """
    from docsearch.core.repository import Repository

    config = ctx.obj["config"]
    repo = Repository(str(config.db_path), config.home)
    count = repo.count()
    click.echo(f"Home:       {config.home}")
    click.echo(f"Database:   {config.db_path}")
    click.echo(f"Indexed documents: {count}")

    if doc_id is not None:
        doc = repo.get_by_id(doc_id)
        if not doc:
            click.echo(f"\nDocument {doc_id} not found.", err=True)
        else:
            import json as _json
            click.echo(f"\nID:             {doc.id}")
            click.echo(f"Path:           {doc.path}")
            click.echo(f"Document type:  {doc.document_type}")
            if verbose:
                click.echo(f"Filename:       {doc.filename}")
                click.echo(f"Directory:      {doc.directory}")
                click.echo(f"Extension:      {doc.extension}")
                click.echo(f"Source type:    {doc.source_type}")
                click.echo(f"Size:           {doc.size:,} bytes")
                click.echo(f"Indexed at:     {doc.indexed_at}")
            meta = doc.combined_metadata
            title = meta.get("title", "")
            if title:
                click.echo(f"Title:          {title.strip()}")
            author = _format_author_slim(meta)
            if author:
                click.echo(f"Author:         {author}")
            year = _extract_year(meta)
            if year:
                click.echo(f"Year:           {year}")
            if verbose:
                click.echo(f"Metadata:\n{_json.dumps(meta, indent=2, default=str)}")

    repo.close()


def _format_author_slim(metadata: dict) -> str | None:
    """Extract a slim author string from metadata."""
    for key in ("author", "authors", "authors_bib"):
        value = metadata.get(key)
        if not value:
            continue
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, list):
            names = [
                f"{v.get('given', '')} {v.get('family', '')}".strip() if isinstance(v, dict) else str(v).strip()
                for v in value
                if v and str(v).strip()
            ]
            if not names:
                continue
            if len(names) > 3:
                return f"{names[0]} et al."
            return ", ".join(names[:3]) or None
    return None


def _extract_year(metadata: dict) -> int | str | None:
    """Extract year from metadata, normalizing to int if possible."""
    year = metadata.get("year")
    if year is None:
        return None
    if isinstance(year, (int, float)):
        return int(year)
    try:
        return int(str(year))
    except (ValueError, TypeError):
        return str(year)


# Register sub-commands
from .commands import (
    index,
    search,
    meta,
    get,
    bibtex,
    papers,
    textbooks,
    ls,
    reference,
    document,
    repair,
)  # noqa: E402
cli.add_command(index)
cli.add_command(search)
cli.add_command(meta)
cli.add_command(get)
cli.add_command(bibtex)
cli.add_command(papers)
cli.add_command(textbooks)
cli.add_command(ls)
cli.add_command(reference.reference)
cli.add_command(document.document)
cli.add_command(repair.repair)


def main() -> None:
    cli()
