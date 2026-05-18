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
@click.pass_context
def info(ctx: click.Context) -> None:
    """Show database location and index statistics."""
    from docsearch.core.repository import Repository

    config = ctx.obj["config"]
    repo = Repository(str(config.db_path))
    count = repo.count()
    click.echo(f"Home:       {config.home}")
    click.echo(f"Database:   {config.db_path}")
    click.echo(f"Indexed documents: {count}")
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
    ls
)  # noqa: E402
cli.add_command(index)
cli.add_command(search)
cli.add_command(meta)
cli.add_command(get)
cli.add_command(bibtex)
cli.add_command(papers)
cli.add_command(textbooks)
cli.add_command(ls)


def main() -> None:
    cli()
