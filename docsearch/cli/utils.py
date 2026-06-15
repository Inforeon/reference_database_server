from __future__ import annotations

from pathlib import Path

import click

from ..config import Config


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
