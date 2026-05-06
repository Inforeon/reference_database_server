from __future__ import annotations

import os
from pathlib import Path

import click

# Default DB location: ~/.local/share/docsearch/docsearch.db on Linux
DEFAULT_DB_DIR = Path(os.path.expanduser("~/.local/share/docsearch"))
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "docsearch.db"


@click.group()
@click.option("--db", type=click.Path(), default=str(DEFAULT_DB_PATH), show_default=True, help="Path to SQLite database.")
@click.pass_context
def cli(ctx: click.Context, db: str) -> None:
    """docsearch — Document metadata index and search engine."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db


@cli.command()
@click.pass_context
def info(ctx: click.Context) -> None:
    """Show database location and index statistics."""
    from docsearch.core.repository import Repository

    repo = Repository(ctx.obj["db_path"])
    count = repo.count()
    click.echo(f"Database: {ctx.obj['db_path']}")
    click.echo(f"Indexed documents: {count}")
    repo.close()


# Register sub-commands
from .commands import index, search, meta  # noqa: E402
cli.add_command(index)
cli.add_command(search)
cli.add_command(meta)


def main() -> None:
    cli()
