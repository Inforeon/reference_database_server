from __future__ import annotations

import json
from pathlib import Path

import click


@click.group(name="meta")
def meta() -> None:
    """Manage sidecar metadata files."""
    pass


@meta.command(name="show")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
def meta_show(filepath: str) -> None:
    """Display the sidecar metadata for a file."""
    sidecar = _find_sidecar(filepath)
    if sidecar and sidecar.is_file():
        with open(sidecar, "r") as f:
            data = json.load(f)
        click.echo(json.dumps(data, indent=2))
    else:
        click.echo(f"No sidecar metadata for {filepath}")


@meta.command(name="set")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option("-k", "--key", required=True, help="Metadata key.")
@click.option("-v", "--value", required=True, help="Metadata value (JSON-encoded if complex).")
def meta_set(filepath: str, key: str, value: str) -> None:
    """Set a key/value pair in the sidecar metadata file."""
    sidecar = _find_sidecar(filepath)
    data = {}
    if sidecar and sidecar.is_file():
        with open(sidecar, "r") as f:
            data = json.load(f)

    # Try to parse value as JSON, fall back to string
    try:
        value = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        pass

    data[key] = value

    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with open(sidecar, "w") as f:
        json.dump(data, f, indent=2)
    click.echo(f"Updated: {sidecar}")


@meta.command(name="delete")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option("-k", "--key", required=True, help="Metadata key to remove.")
def meta_delete(filepath: str, key: str) -> None:
    """Delete a key from the sidecar metadata file."""
    sidecar = _find_sidecar(filepath)
    if not (sidecar and sidecar.is_file()):
        click.echo(f"No sidecar metadata for {filepath}", err=True)
        return

    with open(sidecar, "r") as f:
        data = json.load(f)

    if key in data:
        del data[key]
        with open(sidecar, "w") as f:
            json.dump(data, f, indent=2)
        click.echo(f"Removed key '{key}' from {sidecar}")
    else:
        click.echo(f"Key '{key}' not found in {sidecar}", err=True)


@meta.command(name="init")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
def meta_init(filepath: str) -> None:
    """Create an empty sidecar metadata file."""
    sidecar = _find_sidecar(filepath)
    with open(sidecar, "w") as f:
        json.dump({}, f)
    click.echo(f"Created: {sidecar}")


def _find_sidecar(filepath: str) -> Path:
    return Path(str(filepath) + ".meta.json")
