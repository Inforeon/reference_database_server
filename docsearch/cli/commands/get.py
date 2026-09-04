from __future__ import annotations

import click

from docsearch.core.repository import Repository
from docsearch.core import slicing


@click.command(name="get")
@click.argument("doc_id", type=int)
@click.option(
    "-f", "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.option(
    "-s", "--sections",
    default=None,
    help="Comma-separated section indices to retrieve (e.g. '0,2').",
)
@click.option(
    "-l", "--lines",
    default=None,
    help="Comma-separated line ranges, e.g. '0-99,200-299'.",
)
@click.pass_obj
def get(ctx: dict, doc_id: int, output_format: str, sections: str | None, lines: str | None) -> None:
    """Retrieve the extracted text content of a document by ID.

    Optionally slice by section indices (``--sections``) or line ranges
    (``--lines``).  Sections are defined via ``meta set-section`` or by
    setting the ``sections`` key in sidecar metadata directly.
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        doc = repo.get_by_id(doc_id)
        if not doc:
            click.echo(f"Document with id {doc_id} not found.", err=True)
            return

        if sections:
            _print_sections(doc, sections.split(","), output_format)
        elif lines:
            _print_lines(doc, lines, output_format)
        elif output_format == "json":
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


def _print_sections(doc, section_indices: list[str], output_format: str) -> None:
    """Print text for one or more named sections."""
    text_lines = slicing.split_lines(doc.full_text)
    sections_map = slicing.get_sections_map(doc.combined_metadata)

    if output_format == "json":
        import json
        results = []
        for idx_str in section_indices:
            idx = int(idx_str.strip())
            sec = next((s for s in sections_map if s["index"] == idx), None)
            if sec is None:
                click.echo(f"Section {idx} not found.", err=True)
                return
            content = slicing.get_section_text(text_lines, sec)
            results.append({
                "section_index": sec["index"],
                "section_name": sec["name"],
                "start": sec["start"],
                "end": sec["end"],
                "content": content,
            })
        click.echo(json.dumps({
            "id": doc.id,
            "path": doc.path,
            "filename": doc.filename,
            "sections": results,
        }, indent=2))
    else:
        for idx_str in section_indices:
            idx = int(idx_str.strip())
            sec = next((s for s in sections_map if s["index"] == idx), None)
            if sec is None:
                click.echo(f"Section {idx} not found.", err=True)
                return
            content = slicing.get_section_text(text_lines, sec)
            end_label = sec["end"] if sec["end"] is not None else "EOF"
            click.echo(f"=== {sec['name']} (lines {sec['start']}–{end_label}) ===")
            click.echo(content)
            click.echo()


def _print_lines(doc, ranges_str: str, output_format: str) -> None:
    """Print text sliced by line ranges."""
    text_lines = slicing.split_lines(doc.full_text)
    content = slicing.slice_lines(text_lines, ranges_str)

    if output_format == "json":
        import json
        click.echo(json.dumps({
            "id": doc.id,
            "path": doc.path,
            "filename": doc.filename,
            "lines": ranges_str,
            "content": content,
        }, indent=2))
    else:
        click.echo(f"--- {doc.filename} (lines {ranges_str}) ---")
        click.echo(content)
