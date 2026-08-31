from __future__ import annotations

import click

from docsearch.core.repair import CheckResult, run
from docsearch.core.repository import Repository

# Findings listed before the summary collapses the rest; -v lifts the cap.
_MAX_LISTED = 20


def _report(results: list[CheckResult], *, applied: bool, verbose: bool) -> int:
    """Print a per-check breakdown and return the total number of findings."""
    total = 0
    for result in results:
        total += len(result.findings)
        if not result.findings:
            click.echo(f"{result.name}: clean")
            continue

        verb = "repaired" if applied else "need repair"
        click.echo(f"{result.name}: {len(result.findings)} rows {verb}")
        click.echo(f"  {result.description}")
        listed = result.findings if verbose else result.findings[:_MAX_LISTED]
        for finding in listed:
            click.echo(f"    {finding.row.label}  [{finding.detail}]")
        hidden = len(result.findings) - len(listed)
        if hidden > 0:
            click.echo(f"    ... and {hidden} more (use -v to list all)")
        if applied:
            click.echo(f"    Rows written: {result.repaired}")

    return total


@click.group(name="repair")
def repair() -> None:
    """Fix corruption docsearch itself introduced in the index.

    Checks only cover damage this program wrote into data it owns — extracted
    text, index rows.  Your own metadata keys are never treated as corruption
    and no check will rewrite them.
    """
    pass


@repair.command(name="check")
@click.option("--check", "names", multiple=True, help="Run only this check (repeatable). Default: all.")
@click.option("-v", "--verbose", is_flag=True, help="List every affected row instead of the first 20.")
@click.pass_obj
def check_cmd(ctx: dict, names: tuple[str, ...], verbose: bool) -> None:
    """Report what needs repairing without changing anything."""
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        results = run(repo, names=list(names) or None, apply=False)
    except KeyError as exc:
        raise click.ClickException(str(exc).strip('"')) from exc
    finally:
        repo.close()

    total = _report(results, applied=False, verbose=verbose)
    if total == 0:
        click.echo("\nNothing to repair.")
    else:
        click.echo(f"\nTotal: {total} rows would be repaired. Run 'docsearch repair apply' to fix them.")


@repair.command(name="apply")
@click.option("--check", "names", multiple=True, help="Apply only this check (repeatable). Default: all.")
@click.option("-v", "--verbose", is_flag=True, help="List every affected row instead of the first 20.")
@click.pass_obj
def apply_cmd(ctx: dict, names: tuple[str, ...], verbose: bool) -> None:
    """Repair the index in place.

    Rewrites stored text only — it does not re-extract source files, so
    ``content_hash``, ``mtime`` and ``indexed_at`` are left untouched. Running it
    twice is harmless: a second pass finds nothing to do.
    """
    config = ctx["config"]
    repo = Repository(str(config.db_path), config.home)
    try:
        results = run(repo, names=list(names) or None, apply=True)
    except KeyError as exc:
        raise click.ClickException(str(exc).strip('"')) from exc
    finally:
        repo.close()

    total = _report(results, applied=True, verbose=verbose)
    if total == 0:
        click.echo("\nNothing to repair.")
    else:
        click.echo(f"\nTotal: {total} rows repaired.")
