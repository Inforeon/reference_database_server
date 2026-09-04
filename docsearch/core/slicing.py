from __future__ import annotations

"""Runtime text slicing over ``full_text`` by line numbers.

Sections are stored as sidecar metadata (key ``sections``).  This module never
touches the database or filesystem — it only transforms strings and dicts.
"""

from typing import Any


def split_lines(full_text: str) -> list[str]:
    """Split extracted text into lines.

    Strips the trailing empty element that a final newline would produce, so
    re-joining with ``'\\n'.join(lines)`` is lossless for text that ends in
    exactly one newline (the common case from PDF extractors).
    """
    lines = full_text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines


def slice_lines(lines: list[str], ranges_str: str) -> str:
    """Reassemble text from comma-separated ``start-end`` line ranges.

    Ranges are inclusive on both ends, e.g. ``"0-99,200-299"``.  A bare number
    (e.g. ``"0-199,300"``) means a single line.  Missing or empty string returns
    the full text.
    """
    if not ranges_str:
        return "\n".join(lines)

    total = len(lines)
    parts: list[str] = []

    for chunk in ranges_str.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            start_s, _, end_s = chunk.partition("-")
            start = int(start_s)
            end = int(end_s) + 1  # inclusive → Python slice
        else:
            start = int(chunk)
            end = start + 1  # single line

        # Clamp to valid range
        start = max(0, min(start, total))
        end = max(0, min(end, total + 1))
        if start < end:
            parts.extend(lines[start:end])

    return "\n".join(parts)


def get_section_text(lines: list[str], section: dict[str, Any]) -> str:
    """Extract text for a single section entry ``{start, end}``.

    ``end`` of ``None`` means "to end of document".  Both bounds are inclusive.
    """
    start = section["start"]
    end = section.get("end")

    total = len(lines)
    s = max(0, min(start, total))
    e = total if end is None else max(0, min(end + 1, total + 1))  # inclusive → slice

    return "\n".join(lines[s:e])


def get_sections_map(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse the ``sections`` key from combined metadata into an ordered list.

    Returns a list of dicts sorted by integer key:
    ``[{index, name, start, end}, ...]``.  Empty list if no sections defined.
    """
    raw = metadata.get("sections")
    if not raw or not isinstance(raw, dict):
        return []

    sections: list[dict[str, Any]] = []
    for idx_str, val in raw.items():
        try:
            idx = int(idx_str)
        except (ValueError, TypeError):
            continue
        if isinstance(val, dict):
            sections.append({
                "index": idx,
                "name": val.get("name", ""),
                "start": val.get("start", 0),
                "end": val.get("end"),
            })

    sections.sort(key=lambda s: s["index"])
    return sections


def reindex_sections(sections_dict: dict[str, dict]) -> dict[str, dict]:
    """Re-key a sections dict with contiguous integer keys starting at 0.

    Preserves insertion order (sorted by original integer key).  Used after
    deleting a section so indices remain valid.
    """
    ordered = get_sections_map({"sections": sections_dict})
    return {str(i): {"name": s["name"], "start": s["start"], "end": s["end"]} for i, s in enumerate(ordered)}
