from __future__ import annotations

"""Location and I/O of sidecar ``.meta.json`` files.

Sidecar metadata is user-editable annotation stored alongside a document.  It
lives in two places that must agree: the ``documents.sidecar_metadata`` column
(what search, tag filters and every read path use) and the ``.meta.json`` file
on disk (what re-indexing reads back, and what makes a collection portable
without the database).

The path convention differs by source type:

- ``file`` / ``reference`` — ``<filepath>.meta.json`` next to the document.
  Reference-only entries have no real file, but they still get a sidecar: their
  metadata is arbitrary key/value data with no column to hold it, and
  re-indexing reads that file back, so it is what makes an edit durable.
- ``directory`` — ``<dir>/<dirname>.meta.json`` inside the directory, since a
  directory-type textbook's document *is* the directory.
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SIDECAR_SUFFIX = ".meta.json"


def sidecar_path(abs_path: Path, source_type: str | None = None) -> Path:
    """Return the sidecar file path for a document's absolute path.

    ``abs_path`` must already be resolved.  Callers holding a home-relative
    path resolve it first — ``(home / doc.path).resolve()``.
    """
    if source_type == "directory":
        return abs_path / f"{abs_path.name}{SIDECAR_SUFFIX}"
    return Path(str(abs_path) + SIDECAR_SUFFIX)


def load_sidecar(path: Path) -> dict[str, Any]:
    """Read a sidecar file, returning ``{}`` when absent or unreadable."""
    if not path.is_file():
        return {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Failed to load sidecar %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        logger.warning("Sidecar %s is not a JSON object; ignoring", path)
        return {}
    return data


def write_sidecar(path: Path, metadata: dict[str, Any]) -> bool:
    """Write ``metadata`` to a sidecar file.

    Fault-tolerant by convention: a read-only database home must not fail an
    edit that already succeeded in the database.  Returns True on success.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(metadata, f, indent=2)
    except IOError as e:
        logger.warning("Failed to save sidecar %s: %s", path, e)
        return False
    return True
