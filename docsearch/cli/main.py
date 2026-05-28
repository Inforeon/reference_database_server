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
@click.pass_context
def info(ctx: click.Context, doc_id: int | None) -> None:
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
            click.echo(f"Filename:       {doc.filename}")
            click.echo(f"Directory:      {doc.directory}")
            click.echo(f"Extension:      {doc.extension}")
            click.echo(f"Document type:  {doc.document_type}")
            click.echo(f"Source type:    {doc.source_type}")
            click.echo(f"Size:           {doc.size:,} bytes")
            click.echo(f"Indexed at:     {doc.indexed_at}")
            click.echo(f"Metadata:\n{_json.dumps(doc.combined_metadata, indent=2, default=str)}")

    repo.close()


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


def main() -> None:
    cli()
