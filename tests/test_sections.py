from __future__ import annotations

"""Tests for document sections (slicing, API, CLI)."""

import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from click.testing import CliRunner

from docsearch.core import slicing
from docsearch.core.models import Document
from docsearch.core.repository import Repository
from docsearch.server.app import create_app
from docsearch.cli.main import cli


# ── Core slicing unit tests ────────────────────────────────────────

class TestSplitLines:
    def test_basic_split(self):
        text = "line1\nline2\nline3"
        assert slicing.split_lines(text) == ["line1", "line2", "line3"]

    def test_trailing_newline_stripped(self):
        text = "line1\nline2\nline3\n"
        assert slicing.split_lines(text) == ["line1", "line2", "line3"]

    def test_empty_text(self):
        assert slicing.split_lines("") == []

    def test_single_line(self):
        assert slicing.split_lines("only") == ["only"]

    def test_roundtrip_preserves_text(self):
        """Joining split lines should reproduce original (for text ending in \n)."""
        text = "a\nb\nc\n"
        lines = slicing.split_lines(text)
        assert "\n".join(lines) + "\n" == text


class TestSliceLines:
    def test_single_range(self):
        lines = ["a", "b", "c", "d", "e"]
        assert slicing.slice_lines(lines, "1-3") == "b\nc\nd"

    def test_multi_range(self):
        lines = ["a", "b", "c", "d", "e"]
        assert slicing.slice_lines(lines, "0-1,3-4") == "a\nb\nd\ne"

    def test_bare_number(self):
        lines = ["first", "second", "third"]
        assert slicing.slice_lines(lines, "1") == "second"

    def test_empty_ranges_returns_all(self):
        lines = ["x", "y"]
        assert slicing.slice_lines(lines, "") == "x\ny"

    def test_clamp_out_of_bounds(self):
        lines = ["a", "b"]
        result = slicing.slice_lines(lines, "1-99")
        assert result == "b"


class TestGetSectionText:
    def test_basic_section(self):
        lines = list("abcdefghij")
        sec = {"start": 2, "end": 5}
        assert slicing.get_section_text(lines, sec) == "c\nd\ne\nf"

    def test_null_end_to_eof(self):
        lines = list("abcde")
        sec = {"start": 3, "end": None}
        assert slicing.get_section_text(lines, sec) == "d\ne"

    def test_single_line_section(self):
        lines = ["x", "y", "z"]
        sec = {"start": 1, "end": 1}
        assert slicing.get_section_text(lines, sec) == "y"


class TestGetSectionsMap:
    def test_parses_ordered_list(self):
        meta = {
            "sections": {
                "0": {"name": "Intro", "start": 0, "end": 10},
                "2": {"name": "Methods", "start": 20, "end": 50},
                "1": {"name": "Background", "start": 11, "end": 19},
            }
        }
        result = slicing.get_sections_map(meta)
        assert len(result) == 3
        assert result[0]["name"] == "Intro"
        assert result[1]["name"] == "Background"
        assert result[2]["name"] == "Methods"

    def test_empty_when_missing(self):
        assert slicing.get_sections_map({}) == []
        assert slicing.get_sections_map({"sections": None}) == []
        assert slicing.get_sections_map({"sections": {}}) == []


class TestReindexSections:
    def test_reindexes_after_deletion(self):
        d = {
            "0": {"name": "A", "start": 0, "end": 10},
            "2": {"name": "C", "start": 20, "end": 30},
            "5": {"name": "F", "start": 60, "end": None},
        }
        result = slicing.reindex_sections(d)
        assert list(result.keys()) == ["0", "1", "2"]
        assert result["0"]["name"] == "A"
        assert result["1"]["name"] == "C"
        assert result["2"]["name"] == "F"


# ── Fixtures for API/CLI tests ─────────────────────────────────────

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
def doc_with_sections(db_home: str, tmp_path) -> Document:
    """Document with multi-line text and sections defined in sidecar."""
    file_path = tmp_path / "sectioned.pdf"
    file_path.write_text("dummy")

    lines = [f"Line {i}" for i in range(100)]
    full_text = "\n".join(lines) + "\n"

    doc = Document(
        path=str(file_path),
        filename="sectioned.pdf",
        directory=str(tmp_path),
        extension="pdf",
        size=500,
        mtime=1700000000.0,
        content_hash="hash123",
        extracted_metadata={"title": "Sectioned Doc"},
        sidecar_metadata={
            "sections": {
                "0": {"name": "Abstract", "start": 0, "end": 9},
                "1": {"name": "Introduction", "start": 10, "end": 49},
                "2": {"name": "Methods", "start": 50, "end": None},
            }
        },
        full_text=full_text,
    )

    db_path = Path(db_home) / "docsearch.db"
    repo = Repository(str(db_path))
    repo.upsert(doc)
    fetched = repo.get(str(file_path))
    repo.close()
    return fetched


@pytest.fixture()
def doc_without_sections(db_home: str, tmp_path) -> Document:
    """Plain document with no sections."""
    file_path = tmp_path / "plain.pdf"
    file_path.write_text("dummy")

    lines = [f"Line {i}" for i in range(50)]
    full_text = "\n".join(lines) + "\n"

    doc = Document(
        path=str(file_path),
        filename="plain.pdf",
        directory=str(tmp_path),
        extension="pdf",
        size=300,
        mtime=1700000000.0,
        content_hash="hash456",
        extracted_metadata={},
        sidecar_metadata={},
        full_text=full_text,
    )

    db_path = Path(db_home) / "docsearch.db"
    repo = Repository(str(db_path))
    repo.upsert(doc)
    fetched = repo.get(str(file_path))
    repo.close()
    return fetched


@pytest.fixture()
def dir_type_textbook(db_home: str, tmp_path) -> Document:
    """Directory-type textbook (should reject sections)."""
    dir_path = tmp_path / "textbook_dir"
    dir_path.mkdir()

    doc = Document(
        path=str(dir_path),
        filename="textbook_dir",
        directory=str(tmp_path),
        extension="",
        document_type="textbook",
        source_type="directory",
        size=0,
        mtime=1700000000.0,
        content_hash="",
        extracted_metadata={"title": "Dir Textbook"},
        sidecar_metadata={},
        full_text="",
    )

    db_path = Path(db_home) / "docsearch.db"
    repo = Repository(str(db_path))
    repo.upsert(doc)
    fetched = repo.get(str(dir_path))
    repo.close()
    return fetched


# ── API tests ──────────────────────────────────────────────────────

class TestContentLines:
    def test_full_text_by_default(self, client, doc_with_sections: Document):
        resp = client.get(f"/api/documents/{doc_with_sections.id}/content")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == doc_with_sections.full_text

    def test_lines_param(self, client, doc_with_sections: Document):
        resp = client.get(
            f"/api/documents/{doc_with_sections.id}/content",
            params={"lines": "0-4"},
        )
        assert resp.status_code == 200
        data = resp.json()
        expected = "\n".join([f"Line {i}" for i in range(5)])
        assert data["content"] == expected

    def test_multi_range_lines(self, client, doc_with_sections: Document):
        resp = client.get(
            f"/api/documents/{doc_with_sections.id}/content",
            params={"lines": "0-2,98-99"},
        )
        assert resp.status_code == 200
        data = resp.json()
        lines = data["content"].split("\n")
        assert lines[0] == "Line 0"
        assert lines[-1] == "Line 99"


class TestListSections:
    def test_list_sections(self, client, doc_with_sections: Document):
        resp = client.get(f"/api/documents/{doc_with_sections.id}/sections")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == doc_with_sections.id
        assert len(data["sections"]) == 3
        assert data["sections"][0]["name"] == "Abstract"
        assert data["sections"][0]["start"] == 0
        assert data["sections"][0]["end"] == 9
        assert data["sections"][0]["line_count"] == 10

    def test_null_end_line_count(self, client, doc_with_sections: Document):
        resp = client.get(f"/api/documents/{doc_with_sections.id}/sections")
        data = resp.json()
        methods = data["sections"][2]
        assert methods["name"] == "Methods"
        assert methods["end"] is None
        assert methods["line_count"] == 50  # lines 50-99

    def test_empty_when_no_sections(self, client, doc_without_sections: Document):
        resp = client.get(f"/api/documents/{doc_without_sections.id}/sections")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sections"] == []

    def test_400_directory_type(self, client, dir_type_textbook: Document):
        resp = client.get(f"/api/documents/{dir_type_textbook.id}/sections")
        assert resp.status_code == 400

    def test_404_missing_doc(self, client):
        resp = client.get("/api/documents/9999/sections")
        assert resp.status_code == 404


class TestGetSection:
    def test_get_section_by_index(self, client, doc_with_sections: Document):
        resp = client.get(f"/api/documents/{doc_with_sections.id}/sections/0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["section_index"] == 0
        assert data["section_name"] == "Abstract"
        assert data["start"] == 0
        assert data["end"] == 9
        lines = data["content"].split("\n")
        assert len(lines) == 10
        assert lines[0] == "Line 0"
        assert lines[-1] == "Line 9"

    def test_get_section_null_end(self, client, doc_with_sections: Document):
        resp = client.get(f"/api/documents/{doc_with_sections.id}/sections/2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["section_name"] == "Methods"
        lines = data["content"].split("\n")
        assert len(lines) == 50  # lines 50-99

    def test_404_bad_index(self, client, doc_with_sections: Document):
        resp = client.get(f"/api/documents/{doc_with_sections.id}/sections/99")
        assert resp.status_code == 404

    def test_404_no_sections(self, client, doc_without_sections: Document):
        resp = client.get(f"/api/documents/{doc_without_sections.id}/sections/0")
        assert resp.status_code == 404


class TestAddSection:
    def test_add_section(self, client, doc_without_sections: Document):
        resp = client.post(
            f"/api/documents/{doc_without_sections.id}/sections",
            json={"name": "First Section", "start": 0, "end": 24},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sections"]) == 1
        assert data["sections"][0]["index"] == 0
        assert data["sections"][0]["name"] == "First Section"

    def test_add_auto_increments_index(self, client, doc_with_sections: Document):
        """Adding to a doc with 3 sections (0,1,2) should create index 3."""
        resp = client.post(
            f"/api/documents/{doc_with_sections.id}/sections",
            json={"name": "Conclusion", "start": 95, "end": 99},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sections"]) == 4
        new_sec = data["sections"][3]
        assert new_sec["index"] == 3
        assert new_sec["name"] == "Conclusion"

    def test_add_400_directory_type(self, client, dir_type_textbook: Document):
        resp = client.post(
            f"/api/documents/{dir_type_textbook.id}/sections",
            json={"name": "X", "start": 0, "end": 10},
        )
        assert resp.status_code == 400


class TestDeleteSection:
    def test_delete_section(self, client, doc_with_sections: Document):
        resp = client.delete(f"/api/documents/{doc_with_sections.id}/sections/1")
        assert resp.status_code == 204

        # Verify reindexing
        list_resp = client.get(f"/api/documents/{doc_with_sections.id}/sections")
        data = list_resp.json()
        assert len(data["sections"]) == 2
        assert data["sections"][0]["index"] == 0
        assert data["sections"][0]["name"] == "Abstract"
        assert data["sections"][1]["index"] == 1
        assert data["sections"][1]["name"] == "Methods"

    def test_delete_last_section_removes_key(self, client, doc_without_sections: Document):
        # Add one section first
        client.post(
            f"/api/documents/{doc_without_sections.id}/sections",
            json={"name": "Only", "start": 0, "end": 10},
        )
        # Delete it
        resp = client.delete(f"/api/documents/{doc_without_sections.id}/sections/0")
        assert resp.status_code == 204

        list_resp = client.get(f"/api/documents/{doc_without_sections.id}/sections")
        assert list_resp.json()["sections"] == []

    def test_delete_404_bad_index(self, client, doc_with_sections: Document):
        resp = client.delete(f"/api/documents/{doc_with_sections.id}/sections/99")
        assert resp.status_code == 404


# ── CLI tests ──────────────────────────────────────────────────────

class TestCLIMetaSections:
    @pytest.fixture()
    def runner_and_home(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        # Create a file to index
        doc_file = home / "test.pdf"
        lines = [f"Line {i}" for i in range(100)]
        doc_file.write_text("\n".join(lines) + "\n")

        db_path = home / "docsearch.db"
        repo = Repository(str(db_path), str(home))
        doc = Document(
            path="test.pdf",
            filename="test.pdf",
            directory="",
            extension="pdf",
            size=500,
            mtime=1700000000.0,
            content_hash="h",
            extracted_metadata={},
            sidecar_metadata={},
            full_text="\n".join(lines) + "\n",
        )
        repo.upsert(doc)
        fetched = repo.get("test.pdf")
        repo.close()

        runner = CliRunner()
        return runner, str(home), fetched.id

    def test_list_sections_empty(self, runner_and_home):
        runner, home, doc_id = runner_and_home
        result = runner.invoke(
            cli, ["--home", home, "meta", "list-sections", "test.pdf"]
        )
        assert result.exit_code == 0
        assert "No sections defined" in result.output

    def test_set_section(self, runner_and_home):
        runner, home, doc_id = runner_and_home
        result = runner.invoke(
            cli, ["--home", home, "meta", "set-section", "test.pdf",
                  "--name", "Abstract", "--start", "0", "--end", "9"]
        )
        assert result.exit_code == 0
        assert "Added section 'Abstract'" in result.output

    def test_set_then_list(self, runner_and_home):
        runner, home, doc_id = runner_and_home
        runner.invoke(
            cli, ["--home", home, "meta", "set-section", "test.pdf",
                  "--name", "Intro", "--start", "0", "--end", "24"]
        )
        result = runner.invoke(
            cli, ["--home", home, "meta", "list-sections", "test.pdf"]
        )
        assert result.exit_code == 0
        assert "Intro" in result.output
        assert "0–24" in result.output

    def test_delete_section(self, runner_and_home):
        runner, home, doc_id = runner_and_home
        # Add two sections
        runner.invoke(
            cli, ["--home", home, "meta", "set-section", "test.pdf",
                  "--name", "A", "--start", "0", "--end", "10"]
        )
        runner.invoke(
            cli, ["--home", home, "meta", "set-section", "test.pdf",
                  "--name", "B", "--start", "11", "--end", "20"]
        )
        # Delete first
        result = runner.invoke(
            cli, ["--home", home, "meta", "delete-section", "test.pdf", "0"]
        )
        assert result.exit_code == 0
        assert "Removed section 0" in result.output

        # Verify reindexing — B should now be index 0
        list_result = runner.invoke(
            cli, ["--home", home, "meta", "list-sections", "test.pdf"]
        )
        assert "B" in list_result.output


class TestCLIGetSections:
    @pytest.fixture()
    def runner_and_doc(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        doc_file = home / "sectioned.pdf"
        lines = [f"Line {i}" for i in range(50)]
        doc_file.write_text("\n".join(lines) + "\n")

        db_path = home / "docsearch.db"
        repo = Repository(str(db_path), str(home))
        doc = Document(
            path="sectioned.pdf",
            filename="sectioned.pdf",
            directory="",
            extension="pdf",
            size=200,
            mtime=1700000000.0,
            content_hash="h",
            extracted_metadata={},
            sidecar_metadata={
                "sections": {
                    "0": {"name": "First", "start": 0, "end": 19},
                    "1": {"name": "Second", "start": 20, "end": None},
                }
            },
            full_text="\n".join(lines) + "\n",
        )
        repo.upsert(doc)
        fetched = repo.get("sectioned.pdf")
        repo.close()

        runner = CliRunner()
        return runner, str(home), fetched.id

    def test_get_sections_flag(self, runner_and_doc):
        runner, home, doc_id = runner_and_doc
        result = runner.invoke(
            cli, ["--home", home, "get", str(doc_id), "--sections", "0"]
        )
        assert result.exit_code == 0
        assert "=== First (lines 0–19) ===" in result.output
        assert "Line 0" in result.output
        assert "Line 19" in result.output

    def test_get_multiple_sections(self, runner_and_doc):
        runner, home, doc_id = runner_and_doc
        result = runner.invoke(
            cli, ["--home", home, "get", str(doc_id), "--sections", "0,1"]
        )
        assert result.exit_code == 0
        assert "=== First" in result.output
        assert "=== Second" in result.output

    def test_get_lines_flag(self, runner_and_doc):
        runner, home, doc_id = runner_and_doc
        result = runner.invoke(
            cli, ["--home", home, "get", str(doc_id), "--lines", "5-9"]
        )
        assert result.exit_code == 0
        assert "Line 5" in result.output
        assert "Line 9" in result.output
        assert "Line 10" not in result.output

    def test_get_sections_json(self, runner_and_doc):
        runner, home, doc_id = runner_and_doc
        result = runner.invoke(
            cli, ["--home", home, "get", str(doc_id),
                  "--sections", "0", "-f", "json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["sections"]) == 1
        assert data["sections"][0]["section_name"] == "First"
