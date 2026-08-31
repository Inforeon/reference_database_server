from __future__ import annotations

"""Tests for the repair checks over stored extracted text."""

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from docsearch.cli.main import cli
from docsearch.core.models import Chapter, Document, SearchQuery
from docsearch.core.repair import (
    ControlCharactersCheck,
    all_checks,
    get_check,
    run,
)
from docsearch.core.repository import Repository


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    h = tmp_path / "db_home"
    h.mkdir()
    return h


@pytest.fixture()
def repo(home: Path):
    r = Repository(str(home / "docsearch.db"), home)
    yield r
    r.close()


# Text with a NUL early on and a distinctive term *after* it, so a repair that
# broke FTS recall would show up as a lost hit rather than passing silently.
CORRUPT = "Normalizing flow preamble \x00 T^{-1}(x) \x0c jacobian density correction"
TAIL_TERM = "jacobian"


def _seed_doc(repo: Repository, path: str = "papers/flow.pdf", text: str = CORRUPT) -> int:
    repo.upsert(
        Document(
            path=path,
            filename=Path(path).name,
            directory=str(Path(path).parent),
            extension="pdf",
            content_hash="hash-of-original-bytes",
            mtime=1700000000.0,
            full_text=text,
        )
    )
    doc_id = repo.get(path).id  # type: ignore[union-attr]
    assert doc_id is not None
    return int(doc_id)


def _seed_chapter(repo: Repository, path: str = "books/mybook", text: str = CORRUPT) -> tuple[int, int]:
    book_id = _seed_doc(repo, path, text="front matter")
    repo.upsert_chapter(
        Chapter(textbook_id=book_id, chapter_index=0, title="Ch One", full_text=text)
    )
    chapter = repo.get_chapter(book_id, 0)
    assert chapter is not None and chapter.id is not None
    return int(book_id), int(chapter.id)


def _sql_length(db: Path, table: str, row_id: int) -> int:
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            f"SELECT length(full_text) AS n FROM {table} WHERE id = ?", (row_id,)
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


class TestControlCharactersCheck:
    def test_clean_text_is_not_flagged(self, repo: Repository):
        _seed_doc(repo, text="plain prose with\nnewlines and\ttabs")

        assert all(not r.findings for r in run(repo))

    def test_finds_nul_laden_document(self, repo: Repository):
        _seed_doc(repo)

        results = run(repo)
        findings = [f for r in results for f in r.findings]
        assert len(findings) == 1
        assert findings[0].row.kind == "document"
        assert "U+0000" in findings[0].detail
        assert "U+000C" in findings[0].detail

    def test_scan_does_not_modify(self, repo: Repository):
        doc_id = _seed_doc(repo)

        run(repo, apply=False)

        assert "\x00" in repo.get_by_id(doc_id).full_text

    def test_apply_strips_controls(self, repo: Repository):
        doc_id = _seed_doc(repo)

        results = run(repo, apply=True)

        assert sum(r.repaired for r in results) == 1
        text = repo.get_by_id(doc_id).full_text
        assert "\x00" not in text and "\x0c" not in text
        assert "Normalizing flow preamble" in text

    def test_apply_preserves_meaningful_whitespace(self, repo: Repository):
        doc_id = _seed_doc(repo, text="line one\nline two\tindented\r\nline three\x00")

        run(repo, apply=True)

        assert repo.get_by_id(doc_id).full_text == "line one\nline two\tindented\r\nline three"

    def test_sql_length_matches_python_after_repair(self, repo: Repository, home: Path):
        """The reported symptom: SQLite measured 6892 where Python saw 173589."""
        doc_id = _seed_doc(repo)

        run(repo, apply=True)

        stored = repo.get_by_id(doc_id).full_text
        assert _sql_length(home / "docsearch.db", "documents", doc_id) == len(stored)

    def test_fts_still_retrieves_the_tail_of_the_document(self, repo: Repository):
        """A term appearing after the NUL must remain searchable."""
        _seed_doc(repo)

        run(repo, apply=True)

        hits = repo.search(SearchQuery(q=TAIL_TERM))
        assert len(hits) == 1

    def test_repairs_chapter_text(self, repo: Repository, home: Path):
        book_id, chapter_id = _seed_chapter(repo)

        results = run(repo, apply=True)

        # The textbook's own front matter is clean; only the chapter is rewritten.
        assert sum(r.repaired for r in results) == 1
        chapter = repo.get_chapter(book_id, 0)
        assert chapter is not None
        assert "\x00" not in chapter.full_text
        assert _sql_length(home / "docsearch.db", "textbook_chapters", chapter_id) == len(chapter.full_text)

    def test_chapter_label_names_the_parent_book(self, repo: Repository):
        book_id, _ = _seed_chapter(repo)

        findings = [f for r in run(repo) for f in r.findings]

        assert any(f.row.kind == "chapter" and f.row.label.startswith("books/mybook ::") for f in findings)
        assert book_id > 0

    def test_is_idempotent(self, repo: Repository):
        _seed_doc(repo)

        run(repo, apply=True)
        second = run(repo, apply=True)

        assert all(not r.findings for r in second)


class TestRepairLeavesIndexingAlone:
    def test_hash_mtime_and_indexed_at_untouched(self, repo: Repository):
        doc_id = _seed_doc(repo)
        before = repo.get_by_id(doc_id)

        run(repo, apply=True)

        after = repo.get_by_id(doc_id)
        assert after.content_hash == "hash-of-original-bytes"
        assert after.mtime == before.mtime == 1700000000.0
        assert after.indexed_at == before.indexed_at


class TestCheckRegistry:
    def test_registry_has_the_text_check(self):
        assert "control-characters" in [c.name for c in all_checks()]
        assert any(isinstance(c, ControlCharactersCheck) for c in all_checks())

    def test_get_check_round_trips(self):
        assert get_check("control-characters").name == "control-characters"

    def test_unknown_check_lists_known_names(self):
        with pytest.raises(KeyError, match="control-characters"):
            get_check("no-such-check")

    def test_names_filter_selects_checks(self, repo: Repository):
        _seed_doc(repo)

        results = run(repo, names=["control-characters"])

        assert [r.name for r in results] == ["control-characters"]
        assert results[0].findings


class TestRepairCli:
    @pytest.fixture()
    def runner(self) -> CliRunner:
        return CliRunner()

    def _invoke(self, runner: CliRunner, home: Path, *args: str):
        return runner.invoke(cli, ["--home", str(home), *args])

    def test_check_reports_without_writing(self, runner: CliRunner, home: Path):
        r = Repository(str(home / "docsearch.db"), home)
        doc_id = _seed_doc(r)
        r.close()

        result = self._invoke(runner, home, "repair", "check")

        assert result.exit_code == 0, result.output
        assert "control-characters" in result.output
        assert "papers/flow.pdf" in result.output
        assert "would be repaired" in result.output

        r = Repository(str(home / "docsearch.db"), home)
        try:
            assert "\x00" in r.get_by_id(doc_id).full_text
        finally:
            r.close()

    def test_apply_fixes_and_reports_count(self, runner: CliRunner, home: Path):
        r = Repository(str(home / "docsearch.db"), home)
        doc_id = _seed_doc(r)
        r.close()

        result = self._invoke(runner, home, "repair", "apply")

        assert result.exit_code == 0, result.output
        assert "1 rows repaired" in result.output

        r = Repository(str(home / "docsearch.db"), home)
        try:
            assert "\x00" not in r.get_by_id(doc_id).full_text
        finally:
            r.close()

    def test_clean_database_says_nothing_to_repair(self, runner: CliRunner, home: Path):
        r = Repository(str(home / "docsearch.db"), home)
        _seed_doc(r, text="nothing wrong here")
        r.close()

        result = self._invoke(runner, home, "repair", "check")

        assert result.exit_code == 0, result.output
        assert "Nothing to repair." in result.output

    def test_unknown_check_is_a_clean_error(self, runner: CliRunner, home: Path):
        result = self._invoke(runner, home, "repair", "check", "--check", "bogus")

        assert result.exit_code != 0
        assert "Unknown check" in result.output
        assert "control-characters" in result.output

    def test_selecting_a_check_by_name(self, runner: CliRunner, home: Path):
        r = Repository(str(home / "docsearch.db"), home)
        _seed_doc(r)
        r.close()

        result = self._invoke(runner, home, "repair", "apply", "--check", "control-characters")

        assert result.exit_code == 0, result.output
        assert "Total: 1 rows repaired." in result.output

    def test_collapse_is_lifted_by_verbose(self, runner: CliRunner, home: Path):
        r = Repository(str(home / "docsearch.db"), home)
        for i in range(25):
            _seed_doc(r, path=f"papers/doc{i:02d}.pdf")
        r.close()

        collapsed = self._invoke(runner, home, "repair", "check")
        detailed = self._invoke(runner, home, "repair", "check", "-v")

        assert "and 5 more" in collapsed.output
        assert "and 5 more" not in detailed.output
        assert "doc24.pdf" in detailed.output
