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
            "authors_bib": [
                {"given": "Jane", "family": "Smith", "sequence": "first"},
            ],
            "bibtex": "@article{smith2024great,\n  author = {Smith, Jane},\n  title = {A Great Paper},\n  year = {2024},\n  doi = {10.1234/great},\n}",
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


class TestPaperEndpoints:
    def test_add_paper(self, client, db_home: str, tmp_path):
        """POST /api/papers/add should index a file as a paper."""
        file_path = tmp_path / "paper.pdf"
        file_path.write_text("pdf content")

        resp = client.post(
            "/api/documents/papers/add",
            json={"filepath": str(file_path), "skip_bib": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "paper.pdf"

    def test_add_paper_with_doi(self, client, db_home: str, tmp_path):
        """POST /api/papers/add with DOI should include it in metadata."""
        file_path = tmp_path / "doi_paper.pdf"
        file_path.write_text("pdf content")

        resp = client.post(
            "/api/documents/papers/add",
            json={
                "filepath": str(file_path),
                "doi": "10.1234/test",
                "skip_bib": True,
            },
        )
        assert resp.status_code == 200

    def test_add_paper_missing_file(self, client):
        """POST /api/papers/add with nonexistent path should 404."""
        resp = client.post(
            "/api/documents/papers/add",
            json={"filepath": "/nonexistent/file.pdf"},
        )
        assert resp.status_code == 404

    def test_upload_paper(self, client, db_home: str):
        """POST /api/papers/upload should save and index a paper."""
        resp = client.post(
            "/api/documents/papers/upload?skip_bib=true",
            files={"file": ("test.pdf", b"%PDF-1.4 fake pdf", "application/pdf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "test.pdf"

    def test_upload_paper_with_doi(self, client, db_home: str):
        """POST /api/papers/upload with DOI query param."""
        resp = client.post(
            "/api/documents/papers/upload?doi=10.1234/my-paper&skip_bib=true",
            files={"file": ("doi_test.pdf", b"%PDF-1.4 fake pdf", "application/pdf")},
        )
        assert resp.status_code == 200

    def test_upload_paper_rejects_path_traversal(self, client, db_home: str):
        """POST /api/papers/upload should reject directory traversal."""
        resp = client.post(
            "/api/documents/papers/upload?directory=../../etc",
            files={"file": ("evil.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert resp.status_code == 400


class TestTextbookEndpoints:
    def test_add_textbook(self, client, db_home: str, tmp_path):
        """POST /api/textbooks/add should index a file as a textbook."""
        file_path = tmp_path / "textbook.pdf"
        file_path.write_text("pdf content")

        resp = client.post(
            "/api/documents/textbooks/add",
            json={"filepath": str(file_path)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "textbook.pdf"

    def test_add_textbook_missing_file(self, client):
        """POST /api/textbooks/add with nonexistent path should 404."""
        resp = client.post(
            "/api/documents/textbooks/add",
            json={"filepath": "/nonexistent/book.pdf"},
        )
        assert resp.status_code == 404

    def test_upload_textbook(self, client, db_home: str):
        """POST /api/textbooks/upload should save and index a textbook."""
        resp = client.post(
            "/api/documents/textbooks/upload",
            files={"file": ("book.pdf", b"%PDF-1.4 fake pdf", "application/pdf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "book.pdf"

    def test_upload_textbook_rejects_path_traversal(self, client, db_home: str):
        """POST /api/textbooks/upload should reject directory traversal."""
        resp = client.post(
            "/api/documents/textbooks/upload?directory=../../tmp",
            files={"file": ("evil.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert resp.status_code == 400


class TestChapterEndpoints:
    """Tests for textbook chapter API endpoints."""

    def _index_textbook_with_chapters(self, client, db_home: str):
        """Create a textbook PDF with TOC, index it, and return its doc_id."""
        import fitz
        from pathlib import Path

        home = Path(db_home)
        pdf_path = home / "test_book.pdf"
        doc = fitz.open()
        for i in range(4):
            page = doc.new_page()
            page.insert_text((72, 72), f"Content of chapter section {i + 1}.")
        doc.set_metadata({"title": "Test Book", "author": "Server Author"})
        toc_data = [[1, "First Chapter", 0], [1, "Second Chapter", 2]]
        doc.set_toc(toc_data)
        doc.save(str(pdf_path))
        doc.close()

        resp = client.post(
            "/api/documents/textbooks/add",
            json={"filepath": str(pdf_path)},
        )
        assert resp.status_code == 200
        return resp.json()["id"]

    def test_list_chapters(self, client, db_home: str):
        """GET /api/documents/{id}/chapters returns chapter list."""
        doc_id = self._index_textbook_with_chapters(client, db_home)

        resp = client.get(f"/api/documents/{doc_id}/chapters")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["chapter_index"] == 0
        assert data[0]["title"] == "First Chapter"
        assert data[1]["title"] == "Second Chapter"

    def test_list_chapters_inherits_metadata(self, client, db_home: str):
        """Chapter metadata should inherit from parent textbook."""
        doc_id = self._index_textbook_with_chapters(client, db_home)

        resp = client.get(f"/api/documents/{doc_id}/chapters")
        assert resp.status_code == 200
        data = resp.json()
        # Parent author should be inherited
        assert data[0]["metadata"]["author"] == "Server Author"

    def test_get_chapter_content(self, client, db_home: str):
        """GET /api/documents/{id}/chapters/{index} returns chapter text."""
        doc_id = self._index_textbook_with_chapters(client, db_home)

        resp = client.get(f"/api/documents/{doc_id}/chapters/0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["chapter_index"] == 0
        assert data["title"] == "First Chapter"
        assert "Content of chapter section 1" in data["full_text"]

    def test_get_missing_chapter_returns_404(self, client, db_home: str):
        """Requesting nonexistent chapter index returns 404."""
        doc_id = self._index_textbook_with_chapters(client, db_home)

        resp = client.get(f"/api/documents/{doc_id}/chapters/99")
        assert resp.status_code == 404

    def test_list_chapters_on_non_textbook_returns_400(self, client, db_home: str):
        """Listing chapters on a non-textbook document returns 400."""
        from pathlib import Path
        home = Path(db_home)
        md_path = home / "note.md"
        md_path.write_text("# A note\nSome content here.")

        resp = client.post(
            "/api/index/add",
            json={"filepath": str(md_path), "document_type": "generic"},
        )
        assert resp.status_code == 200
        doc_id = resp.json()["id"]

        resp = client.get(f"/api/documents/{doc_id}/chapters")
        assert resp.status_code == 400

    def test_search_returns_separated_groups(self, client, db_home: str):
        """Search response should have documents and chapters groups."""
        resp = client.get("/api/search?q=test")
        assert resp.status_code == 200
        data = resp.json()
        assert "documents" in data
        assert "chapters" in data
        assert "results" in data["documents"]
        assert "results" in data["chapters"]
        assert "total" in data["documents"]
        assert "total" in data["chapters"]

    def test_search_chapters_by_text(self, client, db_home: str):
        """Search should find textbook chapters by full-text content."""
        self._index_textbook_with_chapters(client, db_home)

        resp = client.get("/api/search?q=chapter+section")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["chapters"]["results"]) >= 1
        # Each result has chapter + parent_document
        hit = data["chapters"]["results"][0]
        assert "chapter" in hit
        assert "parent_document" in hit
        assert hit["parent_document"]["document_type"] == "textbook"
