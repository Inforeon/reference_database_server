from __future__ import annotations

import click

from docsearch.core.repository import Repository


@click.command(name="bibtex")
@click.argument("doc_id", type=int)
@click.pass_obj
def bibtex(ctx: dict, doc_id: int) -> None:
    """Export BibTeX for a research paper by document ID."""
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            click.echo(f"Document with id {doc_id} not found.", err=True)
            return

        if doc.document_type != "paper":
            click.echo(f"Document {doc_id} is not a paper (type={doc.document_type}).", err=True)
            return

        bibtex_str = doc.sidecar_metadata.get("bibtex")
        if not bibtex_str:
            # Fallback: generate from available metadata
            from docsearch.core.handlers import _generate_bibtex_from_metadata

            combined = doc.combined_metadata
            bibtex_str = _generate_bibtex_from_metadata(combined)

        click.echo(bibtex_str)
    finally:
        repo.close()
