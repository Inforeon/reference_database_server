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
    contain further ``=`` characters.  Malformed pairs cause an immediate
    abort — partial silent drop is worse than hard failure.
    """
    meta: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise click.ClickException(f"Invalid metadata pair: {pair} (expected KEY=VALUE)")
        key, value = pair.split("=", 1)
        meta[key] = parse_meta_value(value)
    return meta or None


def parse_breakpoints(raw: str) -> list[dict[str, Any]]:
    """Parse chapter breakpoints into the ``chapters`` format for textbook indexing.

    Accepts two JSON formats:

    - **List** ``[5, 10, 15]`` — page boundaries producing N+1 auto-named
      chapters. The first runs from page 0 to the first breakpoint, and the
      last extends to end of book (``end_page: null``).
    - **Dict** ``{"Intro": 5, "Methods": null}`` — title-to-end-page mapping.
      Entries are sorted by end page (``null`` last); each chapter starts where
      the previous one ended.

    Returns a list of chapter dicts suitable for passing as ``extra_metadata["chapters"]``.
    """
    try:
        breakpoints = json.loads(raw)
    except json.JSONDecodeError:
        raise click.ClickException(
            f"Breakpoints must be valid JSON (list or dict), got: {raw}"
        )

    if isinstance(breakpoints, dict):
        # Dict: {"chp1": 2, "chp2": 5, ..., "last": None}
        # Values are end pages; sorted by value (None last).
        sorted_items = sorted(
            breakpoints.items(),
            key=lambda x: (x[1] is None, x[1] or 0),
        )
        chapters: list[dict[str, Any]] = []
        prev_end = 0
        for i, (title, end_page) in enumerate(sorted_items):
            chapters.append({
                "title": title,
                "start_page": prev_end,
                "end_page": end_page,
            })
            if end_page is not None:
                prev_end = end_page
        return chapters

    if isinstance(breakpoints, list):
        # List: [2, 5, 6, 9] — page boundaries implying N+1 chapters.
        chapters = []
        prev_end = 0
        for i, bp in enumerate(breakpoints):
            chapters.append({
                "title": f"Chapter {i + 1}",
                "start_page": prev_end,
                "end_page": bp,
            })
            prev_end = bp
        # Final chapter from last breakpoint to end of book
        chapters.append({
            "title": f"Chapter {len(breakpoints) + 1}",
            "start_page": prev_end,
            "end_page": None,
        })
        return chapters

    raise click.ClickException(
        f"Breakpoints must be a JSON list or dict, got {type(breakpoints).__name__}"
    )


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


def document_path_candidates(config: Config, user_path: str) -> list[Path]:
    """Absolute paths a user-supplied document path could mean, best guess first.

    Relative input is resolved against the current working directory first —
    the shell's own reading, which keeps ``cd home/sub && docsearch meta show
    file.pdf`` natural — and then against the database home, so a path typed
    from anywhere else on the filesystem still finds its document instead of
    silently matching nothing.  Absolute input yields one candidate.

    Callers decide what to do when no candidate matches; reporting the tried
    paths is what makes a mistyped or wrongly-rooted path obvious, so keep them
    in the returned list rather than filtering ahead of time.
    """
    p = Path(user_path)
    if p.is_absolute():
        return [p.resolve()]

    candidates = [(Path.cwd() / p).resolve(), (config.home.resolve() / p).resolve()]
    seen: set[Path] = set()
    unique: list[Path] = []
    for cand in candidates:
        if cand not in seen:
            seen.add(cand)
            unique.append(cand)
    return unique


def relative_to_home(config: Config, abs_path: Path) -> str | None:
    """Path of ``abs_path`` relative to the database home, or None if outside it."""
    try:
        return str(abs_path.resolve().relative_to(config.home.resolve()))
    except ValueError:
        return None


def describe_candidates(user_path: str, candidates: list[Path]) -> str:
    """One-line account of where a path was looked for, for error messages."""
    if len(candidates) == 1:
        return f"'{user_path}' → {candidates[0]}"
    return f"'{user_path}' → " + ", ".join(str(c) for c in candidates)
