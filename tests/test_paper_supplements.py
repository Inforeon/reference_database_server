"""Tests for directory-type papers with supplementary material."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from click.testing import CliRunner
from starlette.testclient import TestClient

from docsearch.cli.main import cli
from docsearch.core.indexer import Indexer
from docsearch.core.models import Document, Supplement
from docsearch.core.repository import Repository
from docsearch.server.app import create_app


@pytest.fixture()
def home(tmp_path):
    """Create a temporary database home."""
    return tmp_path / "home"


@pytest.fixture()
def db(home):
    """Create a repository in the temporary home."""
    repo = Repository(str(home / "docsearch.db"), str(home))
    yield repo
    repo.close()


@pytest.fixture()
def indexer(db, home):
    return Indexer(db, home)


@pytest.fixture()
def app_client(home):
    """Create a test client with the temporary home."""
    os.environ["DOCSEARCH_HOME"] = str(home)
    os.environ["DOCSEARCH_DB_PATH"] = str(home / "docsearch.db")
    app = create_app()
    yield app
    os.environ.pop("DOCSEARCH_HOME", None)
    os.environ.pop("DOCSEARCH_DB_PATH", None)


@pytest.fixture()
def cli_runner(home):
    """Create a CLI runner with the temporary home."""
    runner = CliRunner()
    os.environ["DOCSEARCH_HOME"] = str(home)
    os.environ["DOCSEARCH_DB_PATH"] = str(home / "docsearch.db")
    yield runner
    os.environ.pop("DOCSEARCH_HOME", None)
    os.environ.pop("DOCSEARCH_DB_PATH", None)


def _create_pdf(path: Path, text: str = "primary paper content"):
    """Create a minimal PDF at the given path."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _create_markdown(path: Path, text: str = "supplement content"):
    """Create a minimal markdown file."""
    path.write_text(text)


class TestSupplementModel:
    """Test the Supplement dataclass."""

    def test_combined_metadata(self):
        parent = Document(
            extracted_metadata={"title": "Parent Title"},
            sidecar_metadata={"author": "Parent Author"},
        )
        sup = Supplement(metadata={"sections": {"0": {"name": "Intro", "start": 0, "end": 10}}})
        combined = sup.combined_metadata(parent)
        assert combined["title"] == "Parent Title"
        assert combined["author"] == "Parent Author"
        assert "sections" in combined

    def test_from_row_dict(self):
        row = {
            "id": 1,
            "paper_id": 42,
            "supplement_index": 0,
            "title": "Appendix A",
            "file_path": "appendix.pdf",
            "metadata": '{"sections": {}}',
            "full_text": "some text",
        }
        sup = Supplement.from_row(row)
        assert sup.id == 1
        assert sup.paper_id == 42
        assert sup.supplement_index == 0
        assert sup.title == "Appendix A"

    def test_from_row_tuple(self):
        row = (1, 42, 0, "Appendix A", "appendix.pdf", "{}", "some text")
        sup = Supplement.from_row(row)
        assert sup.id == 1
        assert sup.paper_id == 42


class TestSupplementRepository:
    """Test repository methods for supplements."""

    def test_upsert_and_get(self, db):
        doc_id = db.upsert(Document(path="papers/test", filename="test", directory="papers"))
        sup = Supplement(paper_id=doc_id, supplement_index=0, title="Appendix", file_path="app.pdf")
        sup_id = db.upsert_supplement(sup)
        assert sup_id > 0

        fetched = db.get_supplement(doc_id, 0)
        assert fetched is not None
        assert fetched.title == "Appendix"

    def test_get_supplements_ordered(self, db):
        doc_id = db.upsert(Document(path="papers/test", filename="test", directory="papers"))
        db.upsert_supplement(Supplement(paper_id=doc_id, supplement_index=1, title="Second"))
        db.upsert_supplement(Supplement(paper_id=doc_id, supplement_index=0, title="First"))

        supplements = db.get_supplements(doc_id)
        assert len(supplements) == 2
        assert supplements[0].title == "First"
        assert supplements[1].title == "Second"

    def test_delete_supplements(self, db):
        doc_id = db.upsert(Document(path="papers/test", filename="test", directory="papers"))
        db.upsert_supplement(Supplement(paper_id=doc_id, supplement_index=0, title="A"))
        db.upsert_supplement(Supplement(paper_id=doc_id, supplement_index=1, title="B"))

        deleted = db.delete_supplements(doc_id)
        assert deleted == 2
        assert db.get_supplements(doc_id) == []

    def test_update_supplement_metadata(self, db):
        doc_id = db.upsert(Document(path="papers/test", filename="test", directory="papers"))
        sup = Supplement(paper_id=doc_id, supplement_index=0, title="A", metadata={"key": "old"})
        sup.id = db.upsert_supplement(sup)

        updated = db.update_supplement_metadata(sup.id, {"key": "new", "extra": "value"})
        assert updated

        fetched = db.get_supplement(doc_id, 0)
        assert fetched.metadata["key"] == "new"
        assert fetched.metadata["extra"] == "value"


class TestPaperDirectoryHandler:
    """Test the PaperDocumentHandler directory handling."""

    def test_handle_directory_with_primary(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        paper_dir = home / "paper_dir"
        paper_dir.mkdir()

        primary_pdf = paper_dir / "main.pdf"
        _create_pdf(primary_pdf, "primary content")
        _create_markdown(paper_dir / "appendix.md", "supplement content")

        # Create sidecar with primary key
        sidecar = paper_dir / "paper_dir.meta.json"
        sidecar.write_text(json.dumps({
            "primary": "main.pdf",
            "title": "Test Paper",
            "author": "Test Author",
        }))

        repo = Repository(str(home / "docsearch.db"), str(home))
        try:
            from docsearch.core.handlers import get_handler
            handler = get_handler("paper", repo, home, extra_metadata={}, skip_bib=True)
            doc = handler.handle(paper_dir)

            assert doc is not None
            assert doc.document_type == "paper"
            assert doc.source_type == "directory"
            assert "Test Paper" in doc.full_text

            supplements = repo.get_supplements(doc.id)
            assert len(supplements) >= 1
        finally:
            repo.close()

    def test_handle_directory_auto_detect_primary(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        paper_dir = home / "paper_dir"
        paper_dir.mkdir()

        # Single PDF - should auto-detect as primary
        _create_pdf(paper_dir / "only.pdf", "primary content")
        _create_markdown(paper_dir / "data.md", "supplement content")

        repo = Repository(str(home / "docsearch.db"), str(home))
        try:
            from docsearch.core.handlers import get_handler
            handler = get_handler("paper", repo, home, extra_metadata={}, skip_bib=True)
            doc = handler.handle(paper_dir)

            assert doc is not None
            assert doc.source_type == "directory"

            # Check sidecar was written with primary
            sidecar = paper_dir / "paper_dir.meta.json"
            meta = json.loads(sidecar.read_text())
            assert meta["primary"] == "only.pdf"
        finally:
            repo.close()


class TestConvertToDirectory:
    """Test the convert_to_directory indexer method."""

    def test_convert_file_to_directory(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()

        # Create a file-type paper
        pdf_path = home / "paper.pdf"
        _create_pdf(pdf_path, "original content")

        repo = Repository(str(home / "docsearch.db"), str(home))
        indexer = Indexer(repo, home)
        doc = indexer.add_file("paper.pdf", document_type="paper", skip_bib=True)
        assert doc is not None

        # Create a supplement file
        sup_path = home / "supplement.md"
        _create_markdown(sup_path, "supplement content")

        # Convert to directory
        converted = indexer.convert_to_directory(doc.id, "supplement.md", "Supplement A")
        assert converted is not None
        assert converted.source_type == "directory"

        # Check directory was created with primary PDF moved in
        paper_dir = home / "paper"
        assert paper_dir.is_dir()
        assert (paper_dir / "paper.pdf").is_file()

        # Check supplements were indexed
        supplements = repo.get_supplements(converted.id)
        assert len(supplements) >= 1

        repo.close()

    def test_convert_already_directory(self, tmp_path):
        """Test that converting an already-directory paper just adds supplement."""
        home = tmp_path / "home"
        home.mkdir()

        # Create a directory-type paper directly
        paper_dir = home / "paper"
        paper_dir.mkdir()
        _create_pdf(paper_dir / "main.pdf", "primary content")

        sidecar = paper_dir / "paper.meta.json"
        sidecar.write_text(json.dumps({"primary": "main.pdf"}))

        repo = Repository(str(home / "docsearch.db"), str(home))
        indexer = Indexer(repo, home)
        doc = indexer.add_file("paper", document_type="paper", skip_bib=True)
        assert doc is not None
        assert doc.source_type == "directory"

        # Create a supplement file
        sup_path = home / "extra.md"
        _create_markdown(sup_path, "extra content")

        # Adding to existing directory should work
        converted = indexer.convert_to_directory(doc.id, "extra.md", "Extra")
        assert converted is not None

        repo.close()


class TestSupplementAPI:
    """Test the REST API endpoints for supplements."""

    def test_list_supplements_empty(self, app_client, home):
        # Create a directory-type paper with no supplements
        repo = Repository(str(home / "docsearch.db"), str(home))
        doc_id = repo.upsert(Document(
            path="papers/test", filename="test", directory="papers",
            document_type="paper", source_type="directory",
        ))
        repo.close()

        resp = TestClient(app_client).get(f"/api/documents/{doc_id}/supplements")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == doc_id
        assert data["supplements"] == []

    def test_list_supplements(self, app_client, home):
        repo = Repository(str(home / "docsearch.db"), str(home))
        doc_id = repo.upsert(Document(
            path="papers/test", filename="test", directory="papers",
            document_type="paper", source_type="directory",
        ))
        repo.upsert_supplement(Supplement(
            paper_id=doc_id, supplement_index=0, title="Appendix A", file_path="app.pdf"
        ))
        repo.close()

        resp = TestClient(app_client).get(f"/api/documents/{doc_id}/supplements")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["supplements"]) == 1
        assert data["supplements"][0]["title"] == "Appendix A"

    def test_get_supplement(self, app_client, home):
        repo = Repository(str(home / "docsearch.db"), str(home))
        doc_id = repo.upsert(Document(
            path="papers/test", filename="test", directory="papers",
            document_type="paper", source_type="directory",
        ))
        repo.upsert_supplement(Supplement(
            paper_id=doc_id, supplement_index=0, title="Appendix",
            full_text="line 0\nline 1\nline 2"
        ))
        repo.close()

        resp = TestClient(app_client).get(f"/api/documents/{doc_id}/supplements/0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Appendix"
        assert data["content"] == "line 0\nline 1\nline 2"

    def test_get_supplement_with_lines(self, app_client, home):
        repo = Repository(str(home / "docsearch.db"), str(home))
        doc_id = repo.upsert(Document(
            path="papers/test", filename="test", directory="papers",
            document_type="paper", source_type="directory",
        ))
        repo.upsert_supplement(Supplement(
            paper_id=doc_id, supplement_index=0, title="Appendix",
            full_text="line 0\nline 1\nline 2"
        ))
        repo.close()

        resp = TestClient(app_client).get(f"/api/documents/{doc_id}/supplements/0?lines=0-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "line 0\nline 1"

    def test_delete_supplement(self, app_client, home):
        repo = Repository(str(home / "docsearch.db"), str(home))
        doc_id = repo.upsert(Document(
            path="papers/test", filename="test", directory="papers",
            document_type="paper", source_type="directory",
        ))
        repo.upsert_supplement(Supplement(
            paper_id=doc_id, supplement_index=0, title="Appendix"
        ))
        repo.close()

        resp = TestClient(app_client).delete(f"/api/documents/{doc_id}/supplements/0")
        assert resp.status_code == 200
        assert resp.json()["deleted"]

    def test_supplement_sections(self, app_client, home):
        repo = Repository(str(home / "docsearch.db"), str(home))
        doc_id = repo.upsert(Document(
            path="papers/test", filename="test", directory="papers",
            document_type="paper", source_type="directory",
        ))
        repo.upsert_supplement(Supplement(
            paper_id=doc_id, supplement_index=0, title="Appendix",
            metadata={"sections": {"0": {"name": "Intro", "start": 0, "end": 5}}},
            full_text="\n".join(f"line {i}" for i in range(10))
        ))
        repo.close()

        # List sections
        resp = TestClient(app_client).get(f"/api/documents/{doc_id}/supplements/0/sections")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sections"]) == 1
        assert data["sections"][0]["name"] == "Intro"

        # Get section content
        resp = TestClient(app_client).get(f"/api/documents/{doc_id}/supplements/0/sections/0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["section_name"] == "Intro"
        lines = data["content"].strip().split("\n")
        assert len(lines) == 6  # lines 0-5

        # Add section
        resp = TestClient(app_client).post(
            f"/api/documents/{doc_id}/supplements/0/sections",
            json={"name": "Methods", "start": 6, "end": 9}
        )
        assert resp.status_code == 200

        # Delete section
        resp = TestClient(app_client).delete(f"/api/documents/{doc_id}/supplements/0/sections/1")
        assert resp.status_code == 200


class TestChapterSectionsAPI:
    """Test the REST API endpoints for chapter sections."""

    def test_chapter_sections(self, app_client, home):
        repo = Repository(str(home / "docsearch.db"), str(home))
        doc_id = repo.upsert(Document(
            path="textbooks/test", filename="test", directory="textbooks",
            document_type="textbook", source_type="directory",
        ))
        from docsearch.core.models import Chapter
        repo.upsert_chapter(Chapter(
            textbook_id=doc_id, chapter_index=0, title="Introduction",
            metadata={"sections": {"0": {"name": "Motivation", "start": 0, "end": 10}}},
            full_text="\n".join(f"line {i}" for i in range(20))
        ))
        repo.close()

        # List sections
        resp = TestClient(app_client).get(f"/api/documents/textbooks/{doc_id}/chapters/0/sections")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sections"]) == 1
        assert data["sections"][0]["name"] == "Motivation"

        # Get section content
        resp = TestClient(app_client).get(f"/api/documents/textbooks/{doc_id}/chapters/0/sections/0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["section_name"] == "Motivation"

        # Add section
        resp = TestClient(app_client).post(
            f"/api/documents/textbooks/{doc_id}/chapters/0/sections",
            json={"name": "Methods", "start": 11, "end": 19}
        )
        assert resp.status_code == 200

        # Delete section
        resp = TestClient(app_client).delete(f"/api/documents/textbooks/{doc_id}/chapters/0/sections/1")
        assert resp.status_code == 200


class TestSupplementCLI:
    """Test CLI commands for supplements."""

    def test_list_supplements_cli(self, cli_runner, home):
        repo = Repository(str(home / "docsearch.db"), str(home))
        doc_id = repo.upsert(Document(
            path="papers/test", filename="test", directory="papers",
            document_type="paper", source_type="directory",
        ))
        repo.upsert_supplement(Supplement(
            paper_id=doc_id, supplement_index=0, title="Appendix A"
        ))
        repo.close()

        result = cli_runner.invoke(cli, ["papers", "list-supplements", str(doc_id)])
        assert result.exit_code == 0
        assert "Appendix A" in result.output

    def test_supplement_text_cli(self, cli_runner, home):
        repo = Repository(str(home / "docsearch.db"), str(home))
        doc_id = repo.upsert(Document(
            path="papers/test", filename="test", directory="papers",
            document_type="paper", source_type="directory",
        ))
        repo.upsert_supplement(Supplement(
            paper_id=doc_id, supplement_index=0, title="Appendix",
            full_text="line 0\nline 1\nline 2"
        ))
        repo.close()

        result = cli_runner.invoke(cli, ["papers", "supplement", str(doc_id), "0"])
        assert result.exit_code == 0
        assert "line 0" in result.output

    def test_supplement_sections_cli(self, cli_runner, home):
        repo = Repository(str(home / "docsearch.db"), str(home))
        doc_id = repo.upsert(Document(
            path="papers/test", filename="test", directory="papers",
            document_type="paper", source_type="directory",
        ))
        repo.upsert_supplement(Supplement(
            paper_id=doc_id, supplement_index=0, title="Appendix",
            metadata={"sections": {"0": {"name": "Intro", "start": 0, "end": 5}}},
            full_text="\n".join(f"line {i}" for i in range(10))
        ))
        repo.close()

        # List sections
        result = cli_runner.invoke(cli, [
            "papers", "supplement", str(doc_id), "0", "--list-sections"
        ])
        assert result.exit_code == 0
        assert "Intro" in result.output

        # Add section
        result = cli_runner.invoke(cli, [
            "papers", "supplement", str(doc_id), "0",
            "--set-section", "Methods", "6", "9"
        ])
        assert result.exit_code == 0
        assert "Added section" in result.output

        # Delete section
        result = cli_runner.invoke(cli, [
            "papers", "supplement", str(doc_id), "0", "--delete-section", "1"
        ])
        assert result.exit_code == 0
        assert "Deleted section" in result.output


class TestChapterSectionsCLI:
    """Test CLI commands for chapter sections."""

    def test_chapter_list_sections(self, cli_runner, home):
        # Create the directory structure first
        (home / "textbooks").mkdir(parents=True, exist_ok=True)
        (home / "textbooks" / "test").touch()

        repo = Repository(str(home / "docsearch.db"), str(home))
        doc_id = repo.upsert(Document(
            path="textbooks/test", filename="test", directory="textbooks",
            document_type="textbook", source_type="directory",
        ))
        from docsearch.core.models import Chapter
        repo.upsert_chapter(Chapter(
            textbook_id=doc_id, chapter_index=0, title="Introduction",
            metadata={"sections": {"0": {"name": "Motivation", "start": 0, "end": 10}}},
            full_text="\n".join(f"line {i}" for i in range(20))
        ))
        repo.close()

        result = cli_runner.invoke(cli, [
            "textbooks", "chapter", str(home / "textbooks" / "test"),
            "-i", "0", "--list-sections"
        ])
        assert result.exit_code == 0
        assert "Motivation" in result.output
