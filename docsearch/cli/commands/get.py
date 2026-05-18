from __future__ import annotations

import click

from docsearch.core.repository import Repository


@click.command(name="get")
@click.argument("doc_id", type=int)
@click.option(
    "-f", "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.pass_obj
def get(ctx: dict, doc_id: int, output_format: str) -> None:
    """Retrieve the extracted text content of a document by ID."""
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            click.echo(f"Document with id {doc_id} not found.", err=True)
            return

        if output_format == "json":
            _print_json(doc)
        else:
            _print_text(doc)
    finally:
        repo.close()


def _print_text(doc) -> None:
    click.echo(f"--- {doc.filename} ({doc.path}) ---")
    click.echo(doc.full_text)


def _print_json(doc) -> None:
    import json
    data = {
        "id": doc.id,
        "path": doc.path,
        "filename": doc.filename,
        "content": doc.full_text,
    }
    click.echo(json.dumps(data, indent=2))
