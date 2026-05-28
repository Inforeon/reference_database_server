from __future__ import annotations

from datetime import datetime

import click

from docsearch.core.models import SearchQuery
from docsearch.core.repository import Repository


@click.command(name="search")
@click.option("-q", "--query", default="", help="Full-text search query.")
@click.option("--scope", default="", help="Restrict search to a subdirectory prefix.")
@click.option("--type", "file_type", default="", help="Filter by file extension (pdf, docx, md…).")
@click.option("--author", default="", help="Filter by author metadata field.")
@click.option("--tag", "tags", multiple=True, help="Filter by tag(s). Can be repeated.")
@click.option("--after", default="", help="Filter documents modified after ISO date (YYYY-MM-DD).")
@click.option("--before", default="", help="Filter documents modified before ISO date (YYYY-MM-DD).")
@click.option("--document-types", default="", help="Filter by document type(s): generic, paper, textbook, reference (comma-separated).")
@click.option("--limit", default=50, type=int, help="Max results to return.")
@click.option("--offset", default=0, type=int, help="Skip N results.")
@click.option(
    "-f", "--format",
    "output_format",
    type=click.Choice(["text", "json", "csv"]),
    default="text",
    help="Output format.",
)
@click.pass_obj
def search(
    ctx: dict,
    query: str,
    scope: str,
    file_type: str,
    author: str,
    tags: tuple[str, ...],
    after: str,
    before: str,
    document_types: str,
    limit: int,
    offset: int,
    output_format: str,
) -> None:
    """Search indexed documents by content and metadata."""
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc_types_list = [t.strip() for t in document_types.split(",") if t.strip()] if document_types else []
        sq = SearchQuery(
            q=query,
            scope=scope,
            file_type=file_type,
            author=author,
            tags=list(tags),
            after=after,
            before=before,
            document_types=doc_types_list,
            limit=limit,
            offset=offset,
        )
        results = repo.search(sq)

        if output_format == "json":
            _print_json(results)
        elif output_format == "csv":
            _print_csv(results)
        else:
            _print_text(results)
    finally:
        repo.close()


def _print_text(results: list) -> None:
    if not results:
        click.echo("No results found.")
        return
    for i, r in enumerate(results, 1):
        d = r.document
        tags_str = ", ".join(tag for tag in get_tags(d) if tag) if get_tags(d) else ""
        author = get_author(d) or ""
        click.echo(f"\n[{i}] {d.filename} (id={d.id})")
        click.echo(f"    Path:    {d.path}")
        click.echo(f"    Type:    {d.extension}  Size: {d.size:,} bytes")
        if author:
            click.echo(f"    Author:  {author}")
        if tags_str:
            click.echo(f"    Tags:    {tags_str}")
        if r.snippet:
            click.echo(f"    Snippet: {r.snippet[:200]}…")


def _print_json(results: list) -> None:
    import json
    data = []
    for r in results:
        d = r.document
        data.append({
            "id": d.id,
            "path": d.path,
            "filename": d.filename,
            "extension": d.extension,
            "size": d.size,
            "metadata": d.combined_metadata,
            "score": r.score,
        })
    click.echo(json.dumps(data, indent=2, default=str))


def _print_csv(results: list) -> None:
    import csv
    import sys
    w = csv.writer(sys.stdout)
    w.writerow(["id", "path", "filename", "extension", "size", "author", "tags", "score"])
    for r in results:
        d = r.document
        w.writerow([
            d.id,
            d.path,
            d.filename,
            d.extension,
            d.size,
            get_author(d) or "",
            ";".join(get_tags(d)),
            r.score,
        ])


def get_tags(doc) -> list[str]:
    tags = doc.combined_metadata.get("tags", [])
    return tags if isinstance(tags, list) else []


def get_author(doc) -> str | None:
    return doc.combined_metadata.get("author")
