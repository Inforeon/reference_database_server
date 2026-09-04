from __future__ import annotations

from datetime import datetime

import click

from docsearch.core.handlers import _format_author_dict
from docsearch.core.models import SearchQuery
from docsearch.core.repository import Repository


@click.command(name="search")
@click.option("-q", "--query", default="", help="Full-text search query.")
@click.option("--scope", default="", help="Restrict search to a subdirectory prefix.")
@click.option("--type", "file_type", default="", help="Filter by file extension (pdf, docx, md…).")
@click.option(
    "--author", default="",
    help="Filter by author. Matches any name in the merged metadata, so partial "
         "names work — 'Schulman' finds 'John Schulman'.",
)
@click.option("--tag", "tags", multiple=True, help="Filter by tag(s). Can be repeated.")
@click.option("--after", default="", help="Filter documents modified after ISO date (YYYY-MM-DD).")
@click.option("--before", default="", help="Filter documents modified before ISO date (YYYY-MM-DD).")
@click.option("--document-types", default="", help="Filter by document type(s): generic, paper, textbook, reference (comma-separated).")
@click.option(
    "--raw-fts", is_flag=True, default=False,
    help="Pass -q to FTS5 verbatim, enabling its query syntax (column filters, "
         "NEAR, OR, ^boost). Off by default: operator characters like '-' then "
         "crash the query instead of being searched for.",
)
@click.option("--limit", default=50, type=int, help="Max results to return.")
@click.option("--offset", default=0, type=int, help="Skip N results.")
@click.option(
    "-f", "--format",
    "output_format",
    type=click.Choice(["text", "json", "csv"]),
    default="text",
    help="Output format.",
)
@click.option(
    "--verbose", is_flag=True, default=False,
    help="Include full metadata in output (default: compact with slim author/year).",
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
    raw_fts: bool,
    limit: int,
    offset: int,
    output_format: str,
    verbose: bool,
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
            raw_fts=raw_fts,
            limit=limit,
            offset=offset,
        )
        results = repo.search(sq)

        if output_format == "json":
            _print_json(results, verbose)
        elif output_format == "csv":
            _print_csv(results)
        else:
            _print_text(results, verbose)
    finally:
        repo.close()


def _print_text(results: list, verbose: bool = False) -> None:
    if not results:
        click.echo("No results found.")
        return
    for i, r in enumerate(results, 1):
        d = r.document
        tags_str = ", ".join(tag for tag in get_tags(d) if tag) if get_tags(d) else ""
        author = get_author(d) if verbose else get_author_slim(d)
        click.echo(f"\n[{i}] {d.filename} (id={d.id})")
        click.echo(f"    Path:    {d.path}")
        if not verbose:
            click.echo(f"    Type:    {d.document_type}")
        else:
            click.echo(f"    Type:    {d.extension}  Size: {d.size:,} bytes")
        if author:
            click.echo(f"    Author:  {author}")
        year = get_year(d)
        if year:
            click.echo(f"    Year:    {year}")
        if tags_str:
            click.echo(f"    Tags:    {tags_str}")
        if r.snippet:
            click.echo(f"    Snippet: {r.snippet[:200]}…")


def _print_json(results: list, verbose: bool = False) -> None:
    import json
    data = []
    for r in results:
        d = r.document
        meta = d.combined_metadata
        entry = {
            "id": d.id,
            "path": d.path,
            "document_type": d.document_type,
            "score": r.score,
        }
        if verbose:
            entry.update({
                "filename": d.filename,
                "extension": d.extension,
                "size": d.size,
                "metadata": meta,
            })
        else:
            title = meta.get("title", "")
            author = _format_author_slim(meta)
            year = _extract_year(meta)
            entry.update({
                "filename": d.filename,
                "title": title.strip() if title else None,
                "author": author,
                "year": year,
            })
        data.append(entry)
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
    """Render the document's authors for display, whatever shape they are stored in.

    Papers hold a plain string, a list of names, or pdf2bib's ``authors_bib``
    dicts depending on how they were added; echoing the raw value printed a
    Python list repr where a byline belongs.  Names are separated with ``"; "``
    because the dict form is itself comma-separated ("Schulman, John").
    """
    meta = doc.combined_metadata
    for key in ("author", "authors", "authors_bib"):
        value = meta.get(key)
        if not value:
            continue
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            names = [
                _format_author_dict(v) if isinstance(v, dict) else str(v)
                for v in value
                if v
            ]
            if names:
                return "; ".join(names)
    return None


def get_author_slim(doc) -> str | None:
    """Render a slim author string (first + 'et al.' if >3)."""
    return _format_author_slim(doc.combined_metadata)


def get_year(doc) -> int | str | None:
    """Extract year from document metadata."""
    return _extract_year(doc.combined_metadata)


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
                _author_dict_name(v) if isinstance(v, dict) else str(v).strip()
                for v in value
                if v and str(v).strip()
            ]
            if not names:
                continue
            if len(names) > 3:
                return f"{names[0]} et al."
            return ", ".join(names[:3]) or None
    return None


def _author_dict_name(d: dict) -> str:
    """Format a single pdf2bib author dict as 'Given Family'."""
    given = d.get("given", "")
    family = d.get("family", "")
    return f"{given} {family}".strip() or str(d)


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
