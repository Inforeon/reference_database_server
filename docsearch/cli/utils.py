from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from ..config import Config


def parse_meta_value(raw: str) -> Any:
    """Parse one ``-m``/``-v`` value, JSON first, raw string as fallback.

    Values that parse as JSON become that JSON type — ``year=2018`` is stored
    as the number 2018 and ``tags=["a","b"]`` as a list.  Everything else stays
    a string.

    To force a string, quote it *inside* the shell so the quotes reach us:
    ``-m arxiv_id='"1706.03762"'``.  This matters for identifier-shaped numbers:
    an unquoted ``arxiv_id=1710.04820`` parses as a float and silently loses the
    trailing zero, since floats have no way to preserve it.
    """
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def parse_meta_pairs(pairs: tuple[str, ...]) -> dict[str, Any] | None:
    """Parse ``-m KEY=VALUE`` pairs into a dict, or ``None`` if there are none.

    A key is taken from everything before the first ``=``, so values may
    contain further ``=`` characters.  Malformed pairs are reported and skipped.
    """
    meta: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            click.echo(f"Invalid metadata pair: {pair} (expected KEY=VALUE)", err=True)
            continue
        key, value = pair.split("=", 1)
        meta[key] = parse_meta_value(value)
    return meta or None


def resolve_user_path_to_home_relative(
    config: Config,
    user_path: str,
    require_exists: bool = False,
    require_file: bool = False,
    require_dir: bool = False,
) -> str:
    """Resolve a user-supplied path to a relative path from the database home.

    Handles the common CLI use case where the user is working from within
    a subdirectory of the database home and provides paths relative to
    their current working directory rather than the database home root.

    Resolution logic:
    - Relative paths are first resolved against ``Path.cwd()``, then made
      relative to the database home.
    - Absolute paths are validated as being under the database home.
    - If ``require_exists`` is set, the resolved path must exist on disk.
    - If ``require_file`` is set, the resolved path must be a regular file.
    - If ``require_dir`` is set, the resolved path must be a directory.

    Raises ``click.ClickException`` with a clear message if the path is
    outside the database home or fails existence/type checks.

    Returns the path as a string relative to ``config.home``, suitable for
    passing directly to :class:`Indexer` methods.
    """
    p = Path(user_path)

    # Resolve against cwd first (handles both absolute and relative input)
    if p.is_absolute():
        abs_path = p.resolve()
    else:
        abs_path = (Path.cwd() / p).resolve()

    # Check containment within database home
    home_resolved = config.home.resolve()
    try:
        rel = abs_path.relative_to(home_resolved)
    except ValueError:
        raise click.ClickException(
            f"Path '{user_path}' resolves to '{abs_path}', which is outside "
            f"the database home ('{home_resolved}'). All indexed files must "
            f"reside within the database home."
        )

    # Existence / type checks
    if require_file:
        if not abs_path.is_file():
            raise click.ClickException(f"'{user_path}' is not a file or does not exist.")
    elif require_dir:
        if not abs_path.is_dir():
            raise click.ClickException(f"'{user_path}' is not a directory or does not exist.")
    elif require_exists:
        if not abs_path.exists():
            raise click.ClickException(f"'{user_path}' does not exist.")

    return str(rel)
