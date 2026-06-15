from __future__ import annotations

"""Tests for CLI paper validation error handling.

Verifies that title mismatch and pdf2bib errors from PaperDocumentHandler
surface as clean error messages in the CLI rather than tracebacks.
"""

import os
from pathlib import Path

import fitz
import pytest
from click.testing import CliRunner

from docsearch.cli.main import cli


def _create_no_title_pdf(tmp_path: Path) -> Path:
    """Create a minimal PDF with no title metadata (mimics LaTeX output)."""
    path = tmp_path / "no_title.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "This is a test paper with no title metadata.")
    # Deliberately set NO metadata — mimics LaTeX-generated PDFs without explicit title
    doc.save(str(path))
    doc.close()
    return path


class TestPapersAddTitleMismatch:
    """Verify CLI surfaces title mismatch errors from pdf2bib."""

    def test_no_pdf_title_surfaces_error(self, tmp_path: Path):
        """papers add with a PDF that has no title should surface an error.

        In non-interactive (test) context, the ambiguity check raises RuntimeError
        because there's no TTY to prompt the user.
        """
        home = tmp_path / "home"
        home.mkdir()
        pdf_path = _create_no_title_pdf(home)

        old_cwd = os.getcwd()
        try:
            os.chdir(str(home))
            runner = CliRunner()

            # Mock pdf2bib to return a title (simulating wrong paper lookup)
            fake_result = {
                "metadata": {"title": "Wrong Paper Title"},
                "bibtex": "@article{wrong, title={Wrong Paper Title}}",
            }
            from unittest.mock import patch

            with patch("pdf2bib.pdf2bib", return_value=fake_result):
                result = runner.invoke(
                    cli, ["--home", str(home), "papers", "add", str(pdf_path)]
                )

            # Non-interactive → should error (no TTY to confirm)
            combined = (result.output + (result.stderr or "")).lower()
            assert result.exit_code != 0, (
                f"Expected non-zero exit, got {result.exit_code}: {result.output}"
            )
            assert "no title metadata" in combined
            assert "--skip-bib" in combined
        finally:
            os.chdir(old_cwd)

    def test_title_mismatch_surfaces_error(self, tmp_path: Path):
        """papers add with mismatched titles should surface an error."""
        home = tmp_path / "home"
        home.mkdir()
        pdf_path = _create_no_title_pdf(home)
        # Overwrite with a PDF that has a title
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "content")
        doc.set_metadata({"title": "Real PDF Title"})
        doc.save(str(pdf_path))
        doc.close()

        old_cwd = os.getcwd()
        try:
            os.chdir(str(home))
            runner = CliRunner()

            fake_result = {
                "metadata": {"title": "Completely Different Title"},
                "bibtex": "@article{fake, title={Completely Different Title}}",
            }
            from unittest.mock import patch

            with patch("pdf2bib.pdf2bib", return_value=fake_result):
                result = runner.invoke(
                    cli, ["--home", str(home), "papers", "add", str(pdf_path)]
                )

            combined = (result.output + (result.stderr or "")).lower()
            assert result.exit_code != 0, (
                f"Expected non-zero exit, got {result.exit_code}: {result.output}"
            )
            assert "title mismatch" in combined
            assert "--skip-bib" in combined
        finally:
            os.chdir(old_cwd)

    def test_skip_bib_avoids_validation(self, tmp_path: Path):
        """papers add --skip-bib should bypass pdf2bib entirely."""
        home = tmp_path / "home"
        home.mkdir()
        pdf_path = _create_no_title_pdf(home)

        old_cwd = os.getcwd()
        try:
            os.chdir(str(home))
            runner = CliRunner()

            # Mock pdf2bib to raise — should NOT be called with skip_bib
            from unittest.mock import patch

            with patch("pdf2bib.pdf2bib", side_effect=Exception("should not be called")):
                result = runner.invoke(
                    cli, ["--home", str(home), "papers", "add", "--skip-bib", str(pdf_path)]
                )

            assert result.exit_code == 0, (
                f"Expected success with --skip-bib, got {result.exit_code}: {result.output}"
            )
            assert "Indexed:" in result.output
        finally:
            os.chdir(old_cwd)


class TestIndexScanPaperTitleMismatch:
    """Verify index scan with -T paper surfaces validation errors."""

    def test_scan_with_paper_type_surfaces_error(self, tmp_path: Path):
        """index scan -T paper should surface no-title errors."""
        home = tmp_path / "home"
        home.mkdir()
        papers_dir = home / "papers"
        papers_dir.mkdir()
        pdf_path = papers_dir / "no_title.pdf"
        # Create PDF with no title metadata
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "content")
        doc.save(str(pdf_path))
        doc.close()

        fake_result = {
            "metadata": {"title": "Wrong Title"},
            "bibtex": "@article{fake, title={Wrong Title}}",
        }

        old_cwd = os.getcwd()
        try:
            os.chdir(str(home))
            runner = CliRunner()

            from unittest.mock import patch

            with patch("pdf2bib.pdf2bib", return_value=fake_result):
                result = runner.invoke(
                    cli, ["--home", str(home), "index", "scan", "-T", "paper", str(papers_dir)]
                )

            # The error should surface (non-zero exit, no TTY to confirm)
            combined = (result.output + (result.stderr or "")).lower()
            assert result.exit_code != 0, (
                f"Expected non-zero exit, got {result.exit_code}: {result.output}"
            )
            assert "no title metadata" in combined
        finally:
            os.chdir(old_cwd)
