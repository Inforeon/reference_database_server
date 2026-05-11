from __future__ import annotations

"""Tests for REST API endpoints."""

import pytest
import httpx
from fastapi.testclient import TestClient

from docsearch.server.app import create_app
from docsearch.core.repository import Repository
from docsearch.core.models import Document


@pytest.fixture()
def db_home(tmp_path):
    """Return a temp directory that serves as the database home."""
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
def indexed_doc(db_home: str, tmp_path) -> Document:
    """Create and index a document with known content."""
    # Create a real file on disk within the db home
    file_path = tmp_path / "test.pdf"
    file_path.write_text("dummy pdf content")

    doc = Document(
        path=str(file_path),
        filename="test.pdf",
        directory=str(tmp_path),
        extension="pdf",
        size=100,
        mtime=1700000000.0,
        content_hash="abc123",
        extracted_metadata={"author": "Test Author"},
        sidecar_metadata={},
        full_text="This is the extracted text content of the document.",
    )

    from pathlib import Path
    db_path = Path(db_home) / "docsearch.db"
    repo = Repository(str(db_path))
    repo.upsert(doc)
    fetched = repo.get(str(file_path))
    repo.close()
    return fetched


@pytest.fixture()
def indexed_paper(db_home: str, tmp_path) -> Document:
    """Create and index a paper-type document with bibtex in sidecar."""
    file_path = tmp_path / "paper.pdf"
    file_path.write_text("dummy paper content")

    doc = Document(
        path=str(file_path),
        filename="paper.pdf",
        directory=str(tmp_path),
        extension="pdf",
        document_type="paper",
        size=200,
        mtime=1700000000.0,
        content_hash="xyz789",
        extracted_metadata={"author": "Smith, Jane"},
        sidecar_metadata={
            "title": "A Great Paper",
            "year": "2024",
            "doi": "10.1234/great",
            "bibtex": "@article{smith2024great,\n  title = {A Great Paper},\n  author = {Smith, Jane},\n  year = {2024},\n  doi = {10.1234/great},\n}",
        },
        full_text="Paper abstract and content here.",
    )

    from pathlib import Path
    db_path = Path(db_home) / "docsearch.db"
    repo = Repository(str(db_path))
    repo.upsert(doc)
    fetched = repo.get(str(file_path))
    repo.close()
    return fetched


class TestGetContent:
    def test_returns_content(self, client, indexed_doc: Document):
        resp = client.get(f"/api/documents/{indexed_doc.id}/content")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == indexed_doc.id
        assert data["filename"] == "test.pdf"
        assert data["content"] == "This is the extracted text content of the document."

    def test_404_missing_document(self, client):
        resp = client.get("/api/documents/9999/content")
        assert resp.status_code == 404


class TestGetFile:
    def test_returns_file(self, client, indexed_doc: Document):
        resp = client.get(f"/api/documents/{indexed_doc.id}/file")
        assert resp.status_code == 200
        assert resp.text.strip() == "dummy pdf content"

    def test_404_missing_document(self, client):
        resp = client.get("/api/documents/9999/file")
        assert resp.status_code == 404

    def test_404_file_not_on_disk(self, client, db_home: str):
        """Document exists in DB but file is gone from disk."""
        from pathlib import Path
        db_path = Path(db_home) / "docsearch.db"
        doc = Document(
            path="/nonexistent/path/gone.pdf",
            filename="gone.pdf",
            directory="/nonexistent/path",
            extension="pdf",
            size=0,
            content_hash="xyz",
            full_text="",
        )
        repo = Repository(str(db_path))
        repo.upsert(doc)
        fetched = repo.get("/nonexistent/path/gone.pdf")
        repo.close()

        resp = client.get(f"/api/documents/{fetched.id}/file")
        assert resp.status_code == 404


class TestUpload:
    def test_upload_txt_and_index(self, client, db_home: str):
        """Upload a text file and verify it gets indexed."""
        resp = client.post(
            "/api/index/upload",
            files={"file": ("hello.txt", b"Hello world content", "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "hello.txt"
        assert data["id"] is not None

        # Verify the file was saved on disk
        import os
        assert os.path.isfile(data["path"])

    def test_upload_in_subdirectory(self, client, db_home: str):
        """Upload to a subdirectory relative to DB home."""
        from pathlib import Path
        root = Path(db_home)
        (root / "papers").mkdir(parents=True, exist_ok=True)

        resp = client.post(
            "/api/index/upload",
            params={"directory": "papers"},
            files={"file": ("thesis.pdf", b"%PDF-1.4 fake pdf", "application/pdf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "thesis.pdf"
        assert "papers" in data["path"]

    def test_upload_with_custom_filename(self, client, db_home: str):
        """Upload with a different filename than the original."""
        resp = client.post(
            "/api/index/upload",
            params={"filename": "renamed.txt"},
            files={"file": ("original.txt", b"content here", "text/plain")},
        )
        assert resp.status_code == 200
        assert resp.json()["filename"] == "renamed.txt"

    def test_upload_rejects_path_traversal_dir(self, client, db_home: str):
        resp = client.post(
            "/api/index/upload",
            params={"directory": "../etc"},
            files={"file": ("evil.txt", b"data", "text/plain")},
        )
        assert resp.status_code == 400

    def test_upload_rejects_path_traversal_filename(self, client, db_home: str):
        resp = client.post(
            "/api/index/upload",
            params={"filename": "../../../tmp/evil.txt"},
            files={"file": ("payload", b"data", "text/plain")},
        )
        assert resp.status_code == 400

    def test_upload_to_nonexistent_directory(self, client, db_home: str):
        resp = client.post(
            "/api/index/upload",
            params={"directory": "does_not_exist"},
            files={"file": ("test.txt", b"data", "text/plain")},
        )
        assert resp.status_code == 400

    def test_upload_unsupported_type(self, client, db_home: str):
        """Uploading an unsupported extension should fail to index."""
        resp = client.post(
            "/api/index/upload",
            files={"file": ("image.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert resp.status_code == 500


class TestBibtexEndpoint:
    def test_returns_bibtex_for_paper(self, client, indexed_paper: Document):
        resp = client.get(f"/api/documents/{indexed_paper.id}/bibtex")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == indexed_paper.id
        assert "@article{smith2024great," in data["bibtex"]

    def test_404_missing_document(self, client):
        resp = client.get("/api/documents/9999/bibtex")
        assert resp.status_code == 404

    def test_400_non_paper_document(self, client, indexed_doc: Document):
        """Generic documents should reject bibtex export."""
        resp = client.get(f"/api/documents/{indexed_doc.id}/bibtex")
        assert resp.status_code == 400

    def test_fallback_bibtex_generation(self, client, db_home: str, tmp_path):
        """Paper without stored bibtex should generate one from metadata."""
        file_path = tmp_path / "nobb.pdf"
        file_path.write_text("no bibtex paper")

        doc = Document(
            path=str(file_path),
            filename="nobb.pdf",
            directory=str(tmp_path),
            extension="pdf",
            document_type="paper",
            size=50,
            mtime=1700000000.0,
            content_hash="nobib",
            extracted_metadata={"author": "No Bib Author"},
            sidecar_metadata={"title": "No BibTeX Paper", "year": "2023"},
            full_text="content",
        )

        from pathlib import Path
        db_path = Path(db_home) / "docsearch.db"
        repo = Repository(str(db_path))
        repo.upsert(doc)
        fetched = repo.get(str(file_path))
        repo.close()

        resp = client.get(f"/api/documents/{fetched.id}/bibtex")
        assert resp.status_code == 200
        data = resp.json()
        assert "@" in data["bibtex"]
        assert "No BibTeX Paper" in data["bibtex"]
