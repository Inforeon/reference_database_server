from __future__ import annotations

"""Repairs for corruption this program introduced in its own index.

The scope is deliberately narrow.  A check may only fix damage ``docsearch``
itself wrote into data it owns — extracted text it mangled, an index row it left
inconsistent.  User-authored metadata is never corruption: an ``arxiv_id`` that
reads as a float instead of a string, a misspelled tag, a missing title are all
the user's data and no check may touch them.

Checks are registered in :data:`_CHECKS` and run through :func:`run`.  A check
that needs something other than a text rewrite should subclass
:class:`RepairCheck` directly; most only need to supply a pure transform via
:class:`TextTransformCheck`.
"""

import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..extractors import sanitize_text
from .models import TextRow
from .repository import Repository


@dataclass(frozen=True)
class Finding:
    """One stored text that a check wants to rewrite."""

    row: TextRow
    detail: str  # what is wrong, phrased for a terminal listing


@dataclass
class CheckResult:
    """Outcome of running one check over the whole index."""

    name: str
    description: str
    findings: list[Finding] = field(default_factory=list)
    repaired: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.findings)


class RepairCheck(ABC):
    """A named scan/fix pair over the index."""

    name: str = ""
    description: str = ""

    @abstractmethod
    def scan(self, repo: Repository) -> list[Finding]:
        """Return everything wrong. Must not modify the database."""
        ...

    @abstractmethod
    def apply(self, repo: Repository, findings: Sequence[Finding]) -> int:
        """Fix the findings, returning how many rows were written."""
        ...


class TextTransformCheck(RepairCheck):
    """Base class for checks that rewrite stored text with a pure function.

    Subclasses implement :meth:`transform`, which returns ``None`` to leave a
    value alone.  ``apply`` re-runs the transform rather than replaying a stored
    replacement, so a repair is idempotent and never writes over text that has
    since been fixed by other means.
    """

    @abstractmethod
    def transform(self, text: str) -> Optional[str]:
        """Return the corrected text, or ``None`` if this value is fine."""
        ...

    def detail(self, original: str) -> str:
        """Describe the problem for the listing. Override for better wording."""
        return "text rewritten"

    def scan(self, repo: Repository) -> list[Finding]:
        findings: list[Finding] = []
        for row in repo.iter_texts():
            if self.transform(row.text) is None:
                continue
            findings.append(Finding(row=row, detail=self.detail(row.text)))
        return findings

    def apply(self, repo: Repository, findings: Sequence[Finding]) -> int:
        repaired = 0
        for finding in findings:
            replacement = self.transform(finding.row.text)
            if replacement is None:
                continue
            if repo.update_text(finding.row, replacement):
                repaired += 1
        return repaired


# Control characters that SQLite/PyMuPDF can leave behind, excluding the
# whitespace controls that carry meaning (tab, newline, carriage return).
_OFFENDING = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _census(text: str) -> str:
    """Summarise which control characters appear, e.g. ``43×U+0000, 69×U+000C``."""
    counts = Counter(_OFFENDING.findall(text))
    if not counts:
        return "control characters"
    parts = [f"{count}×U+{ord(char):04X}" for char, count in sorted(counts.items())]
    return ", ".join(parts)


class ControlCharactersCheck(TextTransformCheck):
    """Strip C0 control characters from stored extracted text.

    PyMuPDF wraps some glyph runs — typically inline math — in U+0000/U+0001
    markers, which older versions of this program stored verbatim.  The harm is
    not just junk in ``get`` output: SQLite's ``length()`` reports a TEXT value
    only up to the first NUL, so a complete document measures as truncated to
    anyone checking it in SQL.

    Rewriting the text does not re-extract the source file, so ``content_hash``,
    ``mtime`` and ``indexed_at`` stay as they were; FTS follows via triggers.
    """

    name = "control-characters"
    description = "Strip C0 control characters from extracted text (documents and chapters)"

    def transform(self, text: str) -> Optional[str]:
        fixed = sanitize_text(text)
        return None if fixed == text else fixed

    def detail(self, original: str) -> str:
        return _census(original)


_CHECKS: tuple[RepairCheck, ...] = (ControlCharactersCheck(),)


def all_checks() -> list[RepairCheck]:
    """Every registered check, in run order."""
    return list(_CHECKS)


def get_check(name: str) -> RepairCheck:
    """Look up a check by name.

    Raises ``KeyError`` listing the valid names — a typo in a repair command
    should not silently do nothing.
    """
    for check in _CHECKS:
        if check.name == name:
            return check
    known = ", ".join(c.name for c in _CHECKS) or "(none registered)"
    raise KeyError(f"Unknown check {name!r}. Known checks: {known}")


def run(
    repo: Repository,
    *,
    names: Sequence[str] | None = None,
    apply: bool = False,
) -> list[CheckResult]:
    """Run checks over the index, optionally writing fixes.

    With ``apply=False`` this is a pure report — nothing is modified.  When
    ``names`` is given only those checks run; an unknown name raises via
    :func:`get_check` rather than being skipped.
    """
    checks = [get_check(name) for name in names] if names else all_checks()

    results: list[CheckResult] = []
    for check in checks:
        findings = check.scan(repo)
        result = CheckResult(name=check.name, description=check.description, findings=findings)
        if apply and findings:
            result.repaired = check.apply(repo, findings)
        results.append(result)
    return results
