from __future__ import annotations

"""Tests for textbook chapter management (set breakpoints, delete chapter)."""

import json
import pytest
import fitz
from pathlib import Path
from fastapi.testclient import TestClient
from click.testing import CliRunner

from docsearch.server.app import create_app
from docsearch.cli.main import cli


# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture()
def db_home(tmp_path):
    home = tmp_path / "docsearch_home"
    home.mkdir(parents=True, exist_ok=True)
    return str(home)


@pytest.fixture()
def app(db_home: str):
    import os
    os.environ["DOCSEARCH_HOME"] = db_home
    _app = create_app()
    yield _app
    os.environ.pop("DOCSEARCH_HOME", None)


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def file_type_textbook(client, db_home: str):
    """Create a file-type textbook with 30 pages and initial chapters."""
    import fitz
    home = Path(db_home)
    pdf_path = home / "file_textbook.pdf"

    doc = fitz.open()
    for i in range(30):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i} content for file textbook.")
    doc.set_metadata({"title": "File Type Textbook"})
    doc.save(str(pdf_path))
    doc.close()

    # Upload with initial breakpoints: [10, 20] → 3 chapters
    resp = client.post(
        "/api/documents/textbooks/upload",
        files={"file": open(pdf_path, "rb")},
        params={"chapter_breakpoints": "[10, 20]"},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


@pytest.fixture()
def dir_type_textbook_with_chapters(client, db_home: str):
    """Create a directory-type textbook with 3 chapter files."""
    home = Path(db_home)

    # Create directory textbook
    resp = client.post(
        "/api/documents/textbooks/upload",
        params={"variant": "directory", "filename": "dir_book"},
    )
    assert resp.status_code == 200
    doc_id = resp.json()["id"]

    # Upload 3 chapters
    for i in range(3):
        chapter_doc = fitz.open()
        page = chapter_doc.new_page()
        page.insert_text((72, 72), f"Chapter {i} content.")
        chapter_doc.save(str(home / f"chapter_{i}.pdf"))
        chapter_doc.close()

        resp = client.post(
            f"/api/documents/textbooks/{doc_id}/chapters/upload",
            files={"file": (f"chapter_{i}.pdf", open(home / f"chapter_{i}.pdf", "rb"), "application/pdf")},
        )
        assert resp.status_code == 200

    return doc_id


# ── API: PUT /{id}/chapters (set breakpoints) ─────────────────────

class TestSetChaptersAPI:
    def test_set_chapters_list_breakpoints(self, client, file_type_textbook):
        """PUT with list breakpoints redefines chapters."""
        # New breakpoints: [5, 15, 25] → 4 chapters
        resp = client.put(
            f"/api/documents/textbooks/{file_type_textbook}/chapters",
            params={"breakpoints": "[5, 15, 25]"},
        )
        assert resp.status_code == 200
        chapters = resp.json()
        assert len(chapters) == 4
        assert chapters[0]["title"] == "Chapter 1"
        assert chapters[0]["start_page"] == 0
        assert chapters[0]["end_page"] == 5
        assert chapters[3]["start_page"] == 25
        assert chapters[3]["end_page"] == 30  # auto-filled to page count

    def test_set_chapters_dict_breakpoints(self, client, file_type_textbook):
        """PUT with dict breakpoints redefines chapters with names."""
        resp = client.put(
            f"/api/documents/textbooks/{file_type_textbook}/chapters",
            params={'breakpoints': '{"Introduction": 9, "Methods": 19, "Conclusion": null}'},
        )
        assert resp.status_code == 200
        chapters = resp.json()
        assert len(chapters) == 3
        assert chapters[0]["title"] == "Introduction"
        assert chapters[0]["end_page"] == 9
        assert chapters[1]["title"] == "Methods"
        assert chapters[2]["title"] == "Conclusion"
        assert chapters[2]["end_page"] == 30  # null → page count

    def test_set_chapters_replaces_existing(self, client, file_type_textbook):
        """PUT replaces all existing chapters."""
        # Initial: 3 chapters from fixture
        list_resp = client.get(f"/api/documents/textbooks/{file_type_textbook}/chapters")
        assert len(list_resp.json()) == 3

        # New: single chapter covering whole book
        resp = client.put(
            f"/api/documents/textbooks/{file_type_textbook}/chapters",
            params={'breakpoints': '{"Full Book": null}'},
        )
        assert resp.status_code == 200
        chapters = resp.json()
        assert len(chapters) == 1
        assert chapters[0]["title"] == "Full Book"

    def test_set_chapters_400_directory_type(self, client, dir_type_textbook_with_chapters):
        """PUT should reject directory-type textbooks."""
        resp = client.put(
            f"/api/documents/textbooks/{dir_type_textbook_with_chapters}/chapters",
            params={"breakpoints": "[5]"},
        )
        assert resp.status_code == 400

    def test_set_chapters_400_not_textbook(self, client, db_home: str):
        """PUT should reject non-textbook documents."""
        from docsearch.core.models import Document
        from docsearch.core.repository import Repository

        home = Path(db_home)
        file_path = home / "paper.pdf"
        file_path.write_text("dummy")
        doc = Document(
            path=str(file_path), filename="paper.pdf", directory=str(home),
            extension="pdf", document_type="paper", size=100, mtime=1700000000.0,
            content_hash="h", extracted_metadata={}, sidecar_metadata={}, full_text="",
        )
        repo = Repository(str(home / "docsearch.db"))
        repo.upsert(doc)
        fetched = repo.get(str(file_path))
        repo.close()

        resp = client.put(
            f"/api/documents/textbooks/{fetched.id}/chapters",
            params={"breakpoints": "[5]"},
        )
        assert resp.status_code == 400

    def test_set_chapters_400_invalid_json(self, client, file_type_textbook):
        """PUT should reject invalid JSON breakpoints."""
        resp = client.put(
            f"/api/documents/textbooks/{file_type_textbook}/chapters",
            params={"breakpoints": "not json"},
        )
        assert resp.status_code == 400


# ── API: DELETE /{id}/chapters/{index} ────────────────────────────

class TestDeleteChapterAPI:
    def test_delete_chapter(self, client, dir_type_textbook_with_chapters):
        """DELETE removes chapter row and physical file."""
        doc_id = dir_type_textbook_with_chapters

        # Verify 3 chapters exist
        list_resp = client.get(f"/api/documents/textbooks/{doc_id}/chapters")
        assert len(list_resp.json()) == 3

        # Delete chapter 1
        resp = client.delete(f"/api/documents/textbooks/{doc_id}/chapters/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["chapter_index"] == 1

        # Verify only 2 remain
        list_resp = client.get(f"/api/documents/textbooks/{doc_id}/chapters")
        assert len(list_resp.json()) == 2

    def test_delete_chapter_removes_file(self, client, dir_type_textbook_with_chapters, db_home: str):
        """DELETE removes the physical chapter file."""
        doc_id = dir_type_textbook_with_chapters
        home = Path(db_home)

        # Get chapter info before deletion
        list_resp = client.get(f"/api/documents/textbooks/{doc_id}/chapters")
        chapters = list_resp.json()
        file_path = chapters[0]["file_path"]

        # Verify file exists
        textbook_dir = home / "dir_book"
        assert (textbook_dir / file_path).is_file()

        # Delete chapter
        client.delete(f"/api/documents/textbooks/{doc_id}/chapters/0")

        # Verify file is gone
        assert not (textbook_dir / file_path).is_file()

    def test_delete_chapter_400_file_type(self, client, file_type_textbook):
        """DELETE should reject file-type textbooks."""
        resp = client.delete(f"/api/documents/textbooks/{file_type_textbook}/chapters/0")
        assert resp.status_code == 400

    def test_delete_chapter_404_missing_chapter(self, client, dir_type_textbook_with_chapters):
        """DELETE should 404 for non-existent chapter."""
        resp = client.delete(f"/api/documents/textbooks/{dir_type_textbook_with_chapters}/chapters/99")
        assert resp.status_code == 404


# ── CLI: merged add command ───────────────────────────────────────

class TestCLIMergedAdd:
    @pytest.fixture()
    def runner_and_home(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        # Create a source PDF outside home
        src_dir = tmp_path / "source"
        src_dir.mkdir()

        pdf_path = src_dir / "textbook.pdf"
        doc = fitz.open()
        for i in range(10):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {i}")
        doc.save(str(pdf_path))
        doc.close()

        runner = CliRunner()
        return runner, str(home), str(pdf_path)

    def test_add_in_place(self, runner_and_home):
        """Add indexes a file that's already in the database home."""
        runner, home, src_pdf = runner_and_home

        # Copy PDF into home first (simulating in-place indexing)
        import shutil
        home_pdf = Path(home) / "textbook.pdf"
        shutil.copy2(src_pdf, home_pdf)

        result = runner.invoke(
            cli, ["--home", home, "textbooks", "add", str(home_pdf)]
        )
        assert result.exit_code == 0
        assert "Indexed:" in result.output

    def test_add_with_directory(self, runner_and_home):
        """Add with -D copies file into subdirectory."""
        runner, home, src_pdf = runner_and_home

        # Create target subdirectory
        (Path(home) / "books").mkdir()

        result = runner.invoke(
            cli, ["--home", home, "textbooks", "add", src_pdf, "-D", "books"]
        )
        assert result.exit_code == 0
        assert "Indexed:" in result.output

        # Verify file was copied
        assert (Path(home) / "books" / "textbook.pdf").is_file()

    def test_add_with_name(self, runner_and_home):
        """Add with -n renames the file."""
        runner, home, src_pdf = runner_and_home

        result = runner.invoke(
            cli, ["--home", home, "textbooks", "add", src_pdf, "-n", "renamed.pdf"]
        )
        assert result.exit_code == 0
        assert "Indexed:" in result.output

        # Verify file was copied with new name
        assert (Path(home) / "renamed.pdf").is_file()


# ── CLI: set-chapters ─────────────────────────────────────────────

class TestCLISetChapters:
    @pytest.fixture()
    def runner_and_textbook(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()

        # Create a multi-page PDF
        pdf_path = home / "textbook.pdf"
        doc = fitz.open()
        for i in range(20):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {i} content.")
        doc.save(str(pdf_path))
        doc.close()

        runner = CliRunner()

        # Index with initial breakpoints
        result = runner.invoke(
            cli, ["--home", home, "textbooks", "add", str(pdf_path),
                  "-b", "[5, 10]"]
        )
        assert result.exit_code == 0

        return runner, str(home), str(pdf_path)

    def test_set_chapters_redefines(self, runner_and_textbook):
        """set-chapters redefines breakpoints."""
        runner, home, pdf_path = runner_and_textbook

        result = runner.invoke(
            cli, ["--home", home, "textbooks", "set-chapters", pdf_path,
                  "-b", '[3, 7, 15]']
        )
        assert result.exit_code == 0
        assert "Updated 4 chapters" in result.output

    def test_set_chapters_named(self, runner_and_textbook):
        """set-chapters with named breakpoints."""
        runner, home, pdf_path = runner_and_textbook

        result = runner.invoke(
            cli, ["--home", home, "textbooks", "set-chapters", pdf_path,
                  "-b", '{"Intro": 4, "Body": null}']
        )
        assert result.exit_code == 0
        assert "Updated 2 chapters" in result.output


# ── CLI: detach-chapter ───────────────────────────────────────────

class TestCLIDetachChapter:
    @pytest.fixture()
    def runner_and_dir_textbook(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()

        runner = CliRunner()

        # Initialize directory textbook
        result = runner.invoke(
            cli, ["--home", home, "textbooks", "init", "dir_book"]
        )
        assert result.exit_code == 0

        # Get doc ID by listing
        from docsearch.core.repository import Repository
        repo = Repository(str(Path(home) / "docsearch.db"), str(home))
        doc = repo.get("dir_book")
        doc_id = doc.id
        repo.close()

        # Attach 2 chapters
        for i in range(2):
            chapter_path = home / f"ch{i}.pdf"
            ch_doc = fitz.open()
            page = ch_doc.new_page()
            page.insert_text((72, 72), f"Chapter {i}")
            ch_doc.save(str(chapter_path))
            ch_doc.close()

            result = runner.invoke(
                cli, ["--home", home, "textbooks", "attach-chapter", str(doc_id), str(chapter_path)]
            )
            assert result.exit_code == 0

        return runner, str(home), doc_id

    def test_detach_chapter(self, runner_and_dir_textbook):
        """detach-chapter removes a chapter."""
        runner, home, doc_id = runner_and_dir_textbook

        result = runner.invoke(
            cli, ["--home", home, "textbooks", "detach-chapter", str(doc_id), "0"]
        )
        assert result.exit_code == 0
        assert "Detached chapter 0" in result.output

        # Verify file is gone
        textbook_dir = Path(home) / "dir_book"
        pdf_files = [f for f in textbook_dir.iterdir() if f.suffix == ".pdf"]
        assert len(pdf_files) == 1  # Only ch1.pdf remains
