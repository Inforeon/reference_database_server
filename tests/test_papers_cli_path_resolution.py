from __future__ import annotations

"""Tests for `papers add` and `textbooks add` CLI path resolution from subdirectories."""

import os
from pathlib import Path

import pytest
import fitz

from docsearch.cli.main import cli


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


@pytest.fixture()
def db_home_with_paper(tmp_path: Path) -> Path:
    """Database home with a real PDF in a subdirectory."""
    home = tmp_path / "home"
    home.mkdir()

    proj1 = home / "proj_1"
    proj2 = home / "proj_2"
    proj1.mkdir()
    proj2.mkdir()

    pdf_path = proj1 / "paper.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Test paper content for indexing.")
    doc.set_metadata({"title": "Test Paper", "author": "Test Author"})
    doc.save(str(pdf_path))
    doc.close()

    return home


@pytest.fixture()
def db_home_with_textbook(tmp_path: Path) -> Path:
    """Database home with a real PDF textbook in a subdirectory."""
    home = tmp_path / "home"
    home.mkdir()
    proj1 = home / "proj_1"
    proj1.mkdir()

    pdf_path = proj1 / "textbook.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Chapter 1 content.")
    doc.set_metadata({"title": "Test Textbook", "author": "Textbook Author"})
    doc.save(str(pdf_path))
    doc.close()

    return home


class TestPapersAddFromSubdirectory:
    """Test `docsearch papers add` from within subdirectories of home."""

    def test_add_from_subdirectory_with_skip_bib(self, db_home_with_paper: Path):
        """From proj_1/, 'papers add paper.pdf --skip-bib' indexes at proj_1/paper.pdf."""
        code, out, err = _run_cli(
            ["--home", str(db_home_with_paper), "papers", "add", "--skip-bib", "paper.pdf"],
            cwd=str(db_home_with_paper / "proj_1"),
        )
        assert code == 0, err
        assert "proj_1/paper.pdf" in out

    def test_add_from_home_root(self, db_home_with_paper: Path):
        """From home/, 'papers add proj_1/paper.pdf --skip-bib' works as before."""
        code, out, err = _run_cli(
            ["--home", str(db_home_with_paper), "papers", "add", "--skip-bib", "proj_1/paper.pdf"],
            cwd=str(db_home_with_paper),
        )
        assert code == 0, err
        assert "proj_1/paper.pdf" in out

    def test_add_absolute_path_within_home(self, db_home_with_paper: Path):
        """Absolute path within home works regardless of cwd."""
        abs_path = str(db_home_with_paper / "proj_1" / "paper.pdf")
        code, out, err = _run_cli(
            ["--home", str(db_home_with_paper), "papers", "add", "--skip-bib", abs_path],
            cwd=str(db_home_with_paper / "proj_2"),
        )
        assert code == 0, err
        assert "proj_1/paper.pdf" in out


class TestTextbooksAddFromSubdirectory:
    """Test `docsearch textbooks add` from within subdirectories of home."""

    def test_add_from_subdirectory(self, db_home_with_textbook: Path):
        """From proj_1/, 'textbooks add textbook.pdf' indexes correctly."""
        code, out, err = _run_cli(
            ["--home", str(db_home_with_textbook), "textbooks", "add", "textbook.pdf"],
            cwd=str(db_home_with_textbook / "proj_1"),
        )
        assert code == 0, err
        assert "proj_1/textbook.pdf" in out
