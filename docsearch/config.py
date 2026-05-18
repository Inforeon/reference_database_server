from __future__ import annotations

import os
from pathlib import Path


_DB_FILENAME = "docsearch.db"


class Config:
    """Central configuration for docsearch.

    The *database home* is the root directory under which all data lives.
    All document paths are resolved relative to the database home.

    The SQLite database file defaults to ``{home}/docsearch.db`` but can be
    placed anywhere via ``DOCSEARCH_DB_PATH`` (useful when home is on a network
    mount with restrictive permissions).

    Override home via ``DOCSEARCH_HOME`` environment variable.
    Override database path via ``DOCSEARCH_DB_PATH`` environment variable.
    """

    def __init__(self, home: str | Path | None = None, db_path: str | Path | None = None):
        if home is not None:
            self.home = Path(home).resolve()
        else:
            self.home = Path(os.environ.get("DOCSEARCH_HOME", "")).resolve() if os.environ.get("DOCSEARCH_HOME") else Path.cwd().resolve()

        if db_path is not None:
            self.db_path = Path(db_path).resolve()
        elif os.environ.get("DOCSEARCH_DB_PATH"):
            self.db_path = Path(os.environ["DOCSEARCH_DB_PATH"]).resolve()
        else:
            self.db_path = self.home / _DB_FILENAME

    @property
    def db_filename(self) -> str:
        return _DB_FILENAME

    def resolve_path(self, path: str | Path) -> Path:
        """Resolve a user-supplied path relative to the database home.

        Returns an absolute path suitable for filesystem operations.
        """
        p = Path(path)
        if p.is_absolute():
            return p.resolve()
        return (self.home / p).resolve()

    def relative_path(self, path: str | Path) -> str:
        """Convert an absolute path to a path relative to the database home.

        If the path is already relative, return it unchanged.
        Raises ValueError if the path does not lie under the database home.
        """
        p = Path(path)
        if not p.is_absolute():
            return str(p)
        try:
            return str(p.resolve().relative_to(self.home))
        except ValueError as e:
            raise ValueError(
                f"Path {p} is not under database home {self.home}"
            ) from e


def default_config() -> Config:
    """Return a Config using environment variables or defaults."""
    return Config()
