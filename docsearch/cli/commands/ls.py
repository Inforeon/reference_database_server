from __future__ import annotations

import json
from pathlib import Path

import click

from docsearch.core.repository import Repository


@click.command(name="ls")
@click.argument("path", default="", type=click.Path())
@click.option(
    "-f", "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.pass_obj
def ls(ctx: dict, path: str, output_format: str) -> None:
    """List indexed contents of a directory.

    Shows files and subdirectories as stored in the index, not the
    filesystem.  PATH is relative to the database home (default: root).
    """
    config = ctx["config"]
    root = Path(config.home).resolve()
    target = (root / path).resolve() if path else root

    # Guard against path traversal outside the database home.
    if not str(target).startswith(str(root)):
        click.echo("Error: path escapes database home", err=True)
        raise SystemExit(1)

    repo = Repository(str(config.db_path))
    try:
        data = repo.list_directory(str(target))
    finally:
        repo.close()

    if output_format == "json":
        _print_json(path, data)
    else:
        _print_text(path, data)


def _print_text(current_path: str, data: dict) -> None:
    header = current_path if current_path else "(root)"
    click.echo(f"Directory: {header}")
    click.echo()

    dirs = data.get("directories", [])
    entries = data.get("entries", [])

    if not dirs and not entries:
        click.echo("  (empty)")
        return

    for d in dirs:
        id_str = f"  (id={d['document_id']})" if d.get("document_id") else ""
        click.echo(f"  [dir]  {d['name']}{id_str}")

    for e in entries:
        click.echo(f"  [file] {e['name']}  (id={e['document_id']})")


def _print_json(current_path: str, data: dict) -> None:
    result = {
        "path": current_path,
        "directories": data.get("directories", []),
        "entries": data.get("entries", []),
    }
    click.echo(json.dumps(result, indent=2))
