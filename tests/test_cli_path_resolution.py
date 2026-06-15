from __future__ import annotations

"""Tests for CLI path resolution relative to current working directory.

Covers the user experience where the user works from within a subdirectory
of the database home and provides paths relative to their cwd rather than
the database home root.
"""

import os
from pathlib import Path

import click
import pytest

from docsearch.cli.main import cli
from docsearch.cli.utils import resolve_user_path_to_home_relative
from docsearch.config import Config


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db_home(tmp_path: Path) -> Path:
    """Create a database home with subdirectories and test files."""
    home = tmp_path / "home"
    home.mkdir()

    proj1 = home / "proj_1"
    proj2 = home / "proj_2"
    proj1.mkdir()
    proj2.mkdir()

    (proj1 / "paper.pdf").write_text("fake pdf content for paper")
    (proj1 / "notes.md").write_text("# Notes\nSome notes here.")

    nested = proj1 / "subdir"
    nested.mkdir()
    (nested / "deep.txt").write_text("deep file content")

    return home


# ── Helper function unit tests ─────────────────────────────────────


class TestResolveUserPathToHomeRelative:
    """Unit tests for the resolve_user_path_to_home_relative helper."""

    @pytest.fixture()
    def config(self, db_home: Path) -> Config:
        return Config(home=str(db_home))

    def test_relative_from_subdirectory(
        self, config: Config, db_home: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """User in home/proj_1/ types 'paper.pdf' → proj_1/paper.pdf."""
        monkeypatch.chdir(str(db_home / "proj_1"))
        result = resolve_user_path_to_home_relative(config, "paper.pdf")
        assert result == "proj_1/paper.pdf"

    def test_relative_from_nested_subdirectory(
        self, config: Config, db_home: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """User in home/proj_1/subdir/ types 'deep.txt' → proj_1/subdir/deep.txt."""
        monkeypatch.chdir(str(db_home / "proj_1" / "subdir"))
        result = resolve_user_path_to_home_relative(config, "deep.txt")
        assert result == "proj_1/subdir/deep.txt"

    def test_relative_with_parent_ref(
        self, config: Config, db_home: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """User in home/proj_1/ types '../proj_2' → proj_2."""
        monkeypatch.chdir(str(db_home / "proj_1"))
        result = resolve_user_path_to_home_relative(config, "../proj_2")
        assert result == "proj_2"

    def test_absolute_path_within_home(
        self, config: Config, db_home: Path
    ):
        """Absolute path within home → relative-to-home."""
        abs_path = str(db_home / "proj_1" / "paper.pdf")
        result = resolve_user_path_to_home_relative(config, abs_path)
        assert result == "proj_1/paper.pdf"

    def test_path_outside_home_raises(
        self, config: Config, db_home: Path
    ):
        """Path outside database home raises ClickException."""
        outside = db_home.parent / "outside_file.txt"
        with pytest.raises(click.ClickException) as exc_info:
            resolve_user_path_to_home_relative(config, str(outside))
        assert "outside" in str(exc_info.value).lower()
        assert "database home" in str(exc_info.value).lower()

    def test_require_file_checks_existence(
        self, config: Config, db_home: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """require_file=True raises for non-existent path."""
        monkeypatch.chdir(str(db_home))
        with pytest.raises(click.ClickException):
            resolve_user_path_to_home_relative(config, "nonexistent.pdf", require_file=True)

    def test_require_file_rejects_directory(
        self, config: Config, db_home: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """require_file=True raises when path is a directory."""
        monkeypatch.chdir(str(db_home))
        with pytest.raises(click.ClickException):
            resolve_user_path_to_home_relative(config, "proj_1", require_file=True)

    def test_require_dir_rejects_file(
        self, config: Config, db_home: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """require_dir=True raises when path is a file."""
        monkeypatch.chdir(str(db_home))
        with pytest.raises(click.ClickException):
            resolve_user_path_to_home_relative(config, "proj_1/paper.pdf", require_dir=True)

    def test_no_existence_check_by_default(
        self, config: Config, db_home: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Without existence flags, non-existent paths within home are accepted."""
        monkeypatch.chdir(str(db_home))
        result = resolve_user_path_to_home_relative(config, "proj_1/future.pdf")
        assert result == "proj_1/future.pdf"

    def test_from_home_root_with_subdir_path(
        self, config: Config, db_home: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """From home root, 'proj_1/paper.pdf' resolves correctly."""
        monkeypatch.chdir(str(db_home))
        result = resolve_user_path_to_home_relative(config, "proj_1/paper.pdf", require_file=True)
        assert result == "proj_1/paper.pdf"


# ── CLI integration tests (use os.chdir for cwd simulation) ────────


def _run_cli(args: list[str], cwd: str):
    """Helper: run the CLI from a given cwd, return (exit_code, output, error)."""
    from click.testing import CliRunner
    old_cwd = os.getcwd()
    try:
        os.chdir(cwd)
        runner = CliRunner()
        result = runner.invoke(cli, args)
        return result.exit_code, result.output, result.stderr or ""
    finally:
        os.chdir(old_cwd)


class TestIndexAddFromSubdirectory:
    """Test `docsearch index add` from within subdirectories of home."""

    def test_add_bare_filename_from_subdirectory(self, db_home: Path):
        """From proj_1/, 'index add paper.pdf' indexes at proj_1/paper.pdf."""
        code, out, err = _run_cli(
            ["--home", str(db_home), "index", "add", "paper.pdf"],
            cwd=str(db_home / "proj_1"),
        )
        assert code == 0, err
        assert "proj_1/paper.pdf" in out

    def test_add_subdir_path_from_home_root(self, db_home: Path):
        """From home/, 'index add proj_1/paper.pdf' works as before."""
        code, out, err = _run_cli(
            ["--home", str(db_home), "index", "add", "proj_1/paper.pdf"],
            cwd=str(db_home),
        )
        assert code == 0, err
        assert "proj_1/paper.pdf" in out

    def test_add_file_outside_home_fails(self, db_home: Path):
        """Adding a file outside database home produces a clear error."""
        outside = db_home.parent / "outside.txt"
        outside.write_text("should fail")
        code, out, err = _run_cli(
            ["--home", str(db_home), "index", "add", str(outside)],
            cwd=str(db_home),
        )
        assert code != 0
        combined = (out + err).lower()
        assert "outside" in combined or "database home" in combined


class TestIndexScanFromSubdirectory:
    """Test `docsearch index scan` from within subdirectories of home."""

    def test_scan_dot_from_subdirectory(self, db_home: Path):
        """'index scan .' from proj_1/ scans proj_1/."""
        code, out, err = _run_cli(
            ["--home", str(db_home), "index", "scan", "."],
            cwd=str(db_home / "proj_1"),
        )
        assert code == 0, err

    def test_scan_parent_from_subdirectory(self, db_home: Path):
        """'index scan ..' from proj_1/ scans the parent (home)."""
        code, out, err = _run_cli(
            ["--home", str(db_home), "index", "scan", ".."],
            cwd=str(db_home / "proj_1"),
        )
        assert code == 0, err


class TestIndexRemoveFromSubdirectory:
    """Test `docsearch index remove` from within subdirectories."""

    def test_remove_from_subdirectory(self, db_home: Path):
        """Add then remove using relative path from same subdir."""
        _run_cli(
            ["--home", str(db_home), "index", "add", "paper.pdf"],
            cwd=str(db_home / "proj_1"),
        )
        code, out, err = _run_cli(
            ["--home", str(db_home), "index", "remove", "paper.pdf"],
            cwd=str(db_home / "proj_1"),
        )
        assert code == 0, err
        assert "Removed:" in out


class TestIndexMoveFromSubdirectory:
    """Test `docsearch index move` with cwd-relative paths and directory destinations."""

    def _index(self, db_home: Path, cwd: Path, rel: str):
        _run_cli(["--home", str(db_home), "index", "add", rel], cwd=str(cwd))

    def test_move_to_file_in_other_dir(self, db_home: Path):
        """move paper.pdf ../proj_2/renamed.pdf — move and rename."""
        self._index(db_home, db_home / "proj_1", "paper.pdf")
        code, out, err = _run_cli(
            ["--home", str(db_home), "index", "move", "paper.pdf", "../proj_2/renamed.pdf"],
            cwd=str(db_home / "proj_1"),
        )
        assert code == 0, err
        assert (db_home / "proj_2" / "renamed.pdf").is_file()
        assert not (db_home / "proj_1" / "paper.pdf").is_file()

    def test_move_to_existing_directory(self, db_home: Path):
        """move paper.pdf ../proj_2 — move into directory, keep name."""
        self._index(db_home, db_home / "proj_1", "paper.pdf")
        code, out, err = _run_cli(
            ["--home", str(db_home), "index", "move", "paper.pdf", "../proj_2"],
            cwd=str(db_home / "proj_1"),
        )
        assert code == 0, err
        assert (db_home / "proj_2" / "paper.pdf").is_file()
        assert not (db_home / "proj_1" / "paper.pdf").is_file()

    def test_move_to_existing_directory_trailing_slash(self, db_home: Path):
        """move paper.pdf ../proj_2/ — same as above with trailing slash."""
        self._index(db_home, db_home / "proj_1", "paper.pdf")
        code, out, err = _run_cli(
            ["--home", str(db_home), "index", "move", "paper.pdf", "../proj_2/"],
            cwd=str(db_home / "proj_1"),
        )
        assert code == 0, err
        assert (db_home / "proj_2" / "paper.pdf").is_file()

    def test_move_to_new_subdirectory(self, db_home: Path):
        """move paper.pdf archived/paper.pdf — create new subdir."""
        self._index(db_home, db_home / "proj_1", "paper.pdf")
        code, out, err = _run_cli(
            ["--home", str(db_home), "index", "move", "paper.pdf", "archived/paper.pdf"],
            cwd=str(db_home / "proj_1"),
        )
        assert code == 0, err
        assert (db_home / "proj_1" / "archived" / "paper.pdf").is_file()

    def test_move_destination_outside_home_fails(self, db_home: Path):
        """Destination outside home raises a clear error."""
        self._index(db_home, db_home / "proj_1", "paper.pdf")
        outside_dir = db_home.parent / "outside"
        outside_dir.mkdir(exist_ok=True)
        code, out, err = _run_cli(
            ["--home", str(db_home), "index", "move", "paper.pdf", str(outside_dir)],
            cwd=str(db_home / "proj_1"),
        )
        assert code != 0
        combined = (out + err).lower()
        assert "outside" in combined or "database home" in combined

    def test_move_from_home_root(self, db_home: Path):
        """From home root: move proj_1/paper.pdf proj_2/moved.pdf."""
        self._index(db_home, db_home, "proj_1/paper.pdf")
        code, out, err = _run_cli(
            ["--home", str(db_home), "index", "move", "proj_1/paper.pdf", "proj_2/moved.pdf"],
            cwd=str(db_home),
        )
        assert code == 0, err
        assert (db_home / "proj_2" / "moved.pdf").is_file()
