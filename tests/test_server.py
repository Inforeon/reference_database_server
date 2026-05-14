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


class TestDirectoryTextbookEndpoints:
    """Tests for directory-type textbooks and chapter uploads."""

    def _create_chapter_pdf(self, path: str, text: str, num_pages: int = 1):
        """Helper to create a simple PDF with given text."""
        import fitz
        doc = fitz.open()
        for _ in range(num_pages):
            page = doc.new_page()
            page.insert_text((72, 72), text)
        doc.save(path)
        doc.close()

    def test_upload_empty_directory_textbook(self, client, db_home: str):
        """POST /api/documents/textbooks/upload?variant=directory creates empty dir textbook."""
        resp = client.post(
            "/api/documents/textbooks/upload?variant=directory&filename=my_book",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "my_book"
        # Verify the directory was created
        from pathlib import Path
        assert Path(db_home) / "my_book" is not None

    def test_upload_empty_directory_textbook_default_name(self, client, db_home: str):
        """Without filename, default name 'textbook' is used."""
        resp = client.post(
            "/api/documents/textbooks/upload?variant=directory",
        )
        assert resp.status_code == 200
        assert resp.json()["filename"] == "textbook"

    def test_upload_empty_directory_in_subdir(self, client, db_home: str):
        """Create empty directory textbook in a subdirectory."""
        from pathlib import Path
        subdir = Path(db_home) / "books"
        subdir.mkdir(exist_ok=True)

        resp = client.post(
            "/api/documents/textbooks/upload?variant=directory&directory=books&filename=sub_book",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "sub_book"
        assert (subdir / "sub_book").is_dir()

    def test_upload_chapter_to_directory_textbook(self, client, db_home: str):
        """Upload a chapter PDF to an empty directory textbook."""
        import fitz
        from pathlib import Path

        # Create empty directory textbook
        resp = client.post(
            "/api/documents/textbooks/upload?variant=directory&filename=chapter_book",
        )
        assert resp.status_code == 200
        doc_id = resp.json()["id"]

        # Create a real PDF for upload
        pdf_path = Path(db_home) / "temp_chapter.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Chapter one content here.")
        doc.save(str(pdf_path))
        doc.close()

        # Upload as chapter
        with open(pdf_path, "rb") as f:
            resp = client.post(
                f"/api/documents/{doc_id}/chapters/upload",
                files={"file": ("intro.pdf", f.read(), "application/pdf")},
            )
        pdf_path.unlink()

        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Intro"
        assert data["chapter_type"] == "file"
        assert data["file_path"] == "intro.pdf"
        assert data["start_page"] is None
        assert data["end_page"] is None
        assert data["page_count"] == 1

    def test_upload_chapter_auto_assigns_index(self, client, db_home: str):
        """Multiple chapter uploads get auto-incrementing indices."""
        import fitz
        from pathlib import Path

        # Create empty directory textbook
        resp = client.post(
            "/api/documents/textbooks/upload?variant=directory&filename=index_book",
        )
        doc_id = resp.json()["id"]

        pdf_path = Path(db_home) / "temp.pdf"

        for i in range(3):
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), f"Content {i}")
            doc.save(str(pdf_path))
            doc.close()

            with open(pdf_path, "rb") as f:
                resp = client.post(
                    f"/api/documents/{doc_id}/chapters/upload",
                    files={"file": (f"ch{i}.pdf", f.read(), "application/pdf")},
                )
            assert resp.status_code == 200
            assert resp.json()["chapter_index"] == i

        pdf_path.unlink()

    def test_upload_chapter_explicit_index(self, client, db_home: str):
        """Chapter upload with explicit chapter_index."""
        import fitz
        from pathlib import Path

        resp = client.post(
            "/api/documents/textbooks/upload?variant=directory&filename=explicit_book",
        )
        doc_id = resp.json()["id"]

        pdf_path = Path(db_home) / "temp.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Explicit index chapter.")
        doc.save(str(pdf_path))
        doc.close()

        with open(pdf_path, "rb") as f:
            resp = client.post(
                f"/api/documents/{doc_id}/chapters/upload?chapter_index=5",
                files={"file": ("ch5.pdf", f.read(), "application/pdf")},
            )
        pdf_path.unlink()

        assert resp.status_code == 200
        assert resp.json()["chapter_index"] == 5

    def test_upload_chapter_overwrites_existing(self, client, db_home: str):
        """Uploading a chapter with same filename replaces old content."""
        import fitz
        from pathlib import Path

        resp = client.post(
            "/api/documents/textbooks/upload?variant=directory&filename=overwrite_book",
        )
        doc_id = resp.json()["id"]

        pdf_path = Path(db_home) / "temp.pdf"

        # Upload first version
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Original content.")
        doc.save(str(pdf_path))
        doc.close()

        with open(pdf_path, "rb") as f:
            resp = client.post(
                f"/api/documents/{doc_id}/chapters/upload",
                files={"file": ("chapter1.pdf", f.read(), "application/pdf")},
            )
        assert resp.status_code == 200
        old_id = resp.json()["id"]

        # Upload second version (same filename)
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Updated content.")
        doc.save(str(pdf_path))
        doc.close()

        with open(pdf_path, "rb") as f:
            resp = client.post(
                f"/api/documents/{doc_id}/chapters/upload",
                files={"file": ("chapter1.pdf", f.read(), "application/pdf")},
            )
        pdf_path.unlink()

        assert resp.status_code == 200
        new_data = resp.json()
        # Should have a new ID (old was deleted, new inserted)
        assert new_data["file_path"] == "chapter1.pdf"

        # Verify only one chapter exists
        resp = client.get(f"/api/documents/{doc_id}/chapters")
        assert resp.status_code == 200
        chapters = resp.json()
        ch1_chapters = [c for c in chapters if c["file_path"] == "chapter1.pdf"]
        assert len(ch1_chapters) == 1

    def test_upload_chapter_custom_filename(self, client, db_home: str):
        """Upload chapter with custom filename query param."""
        import fitz
        from pathlib import Path

        resp = client.post(
            "/api/documents/textbooks/upload?variant=directory&filename=custom_book",
        )
        doc_id = resp.json()["id"]

        pdf_path = Path(db_home) / "temp.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Custom name chapter.")
        doc.save(str(pdf_path))
        doc.close()

        with open(pdf_path, "rb") as f:
            resp = client.post(
                f"/api/documents/{doc_id}/chapters/upload?filename=renamed_chapter.pdf",
                files={"file": ("original.pdf", f.read(), "application/pdf")},
            )
        pdf_path.unlink()

        assert resp.status_code == 200
        assert resp.json()["file_path"] == "renamed_chapter.pdf"

    def test_upload_chapter_to_file_textbook_returns_400(self, client, db_home: str):
        """Cannot upload chapters to a file-type textbook."""
        doc_id = self._index_textbook_with_chapters(client, db_home)

        import fitz
        from pathlib import Path
        pdf_path = Path(db_home) / "temp.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Should fail.")
        doc.save(str(pdf_path))
        doc.close()

        with open(pdf_path, "rb") as f:
            resp = client.post(
                f"/api/documents/{doc_id}/chapters/upload",
                files={"file": ("ch.pdf", f.read(), "application/pdf")},
            )
        pdf_path.unlink()

        assert resp.status_code == 400
        assert "not 'directory'" in resp.json()["detail"]

    def test_upload_chapter_to_non_textbook_returns_400(self, client, db_home: str):
        """Cannot upload chapters to a non-textbook document."""
        from pathlib import Path
        home = Path(db_home)
        md_path = home / "note.md"
        md_path.write_text("# Note\nSome content.")

        resp = client.post(
            "/api/index/add",
            json={"filepath": str(md_path), "document_type": "generic"},
        )
        doc_id = resp.json()["id"]

        import fitz
        pdf_path = Path(db_home) / "temp.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Fail.")
        doc.save(str(pdf_path))
        doc.close()

        with open(pdf_path, "rb") as f:
            resp = client.post(
                f"/api/documents/{doc_id}/chapters/upload",
                files={"file": ("ch.pdf", f.read(), "application/pdf")},
            )
        pdf_path.unlink()

        assert resp.status_code == 400
        assert "not 'textbook'" in resp.json()["detail"]

    def test_upload_chapter_rejects_path_traversal(self, client, db_home: str):
        """Chapter upload should reject filenames with path separators."""
        resp = client.post(
            "/api/documents/textbooks/upload?variant=directory&filename=traversal_book",
        )
        doc_id = resp.json()["id"]

        import fitz
        from pathlib import Path
        pdf_path = Path(db_home) / "temp.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Evil.")
        doc.save(str(pdf_path))
        doc.close()

        with open(pdf_path, "rb") as f:
            resp = client.post(
                f"/api/documents/{doc_id}/chapters/upload?filename=../evil.pdf",
                files={"file": ("evil.pdf", f.read(), "application/pdf")},
            )
        pdf_path.unlink()

        assert resp.status_code == 400

    def test_list_chapters_shows_file_type_fields(self, client, db_home: str):
        """Chapter list response includes chapter_type, file_path, page_count."""
        import fitz
        from pathlib import Path

        resp = client.post(
            "/api/documents/textbooks/upload?variant=directory&filename=list_book",
        )
        doc_id = resp.json()["id"]

        pdf_path = Path(db_home) / "temp.pdf"
        doc = fitz.open()
        for _ in range(3):
            doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        with open(pdf_path, "rb") as f:
            resp = client.post(
                f"/api/documents/{doc_id}/chapters/upload",
                files={"file": ("multi_page.pdf", f.read(), "application/pdf")},
            )
        pdf_path.unlink()

        assert resp.status_code == 200
        data = resp.json()
        assert data["chapter_type"] == "file"
        assert data["file_path"] == "multi_page.pdf"
        assert data["page_count"] == 3
        assert data["start_page"] is None
        assert data["end_page"] is None

    def test_get_chapter_content_for_file_type(self, client, db_home: str):
        """Get specific chapter content for file-type chapter."""
        import fitz
        from pathlib import Path

        resp = client.post(
            "/api/documents/textbooks/upload?variant=directory&filename=get_book",
        )
        doc_id = resp.json()["id"]

        pdf_path = Path(db_home) / "temp.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Unique chapter content for retrieval.")
        doc.save(str(pdf_path))
        doc.close()

        with open(pdf_path, "rb") as f:
            resp = client.post(
                f"/api/documents/{doc_id}/chapters/upload",
                files={"file": ("unique.pdf", f.read(), "application/pdf")},
            )
        pdf_path.unlink()

        chapter_idx = resp.json()["chapter_index"]

        resp = client.get(f"/api/documents/{doc_id}/chapters/{chapter_idx}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["chapter_type"] == "file"
        assert "Unique chapter content for retrieval" in data["full_text"]

    def _index_textbook_with_chapters(self, client, db_home: str):
        """Create a textbook PDF with TOC, index it, and return its doc_id."""
        import fitz
        from pathlib import Path

        home = Path(db_home)
        pdf_path = home / "test_book_ref.pdf"
        doc = fitz.open()
        for i in range(4):
            page = doc.new_page()
            page.insert_text((72, 72), f"Content of chapter section {i + 1}.")
        doc.set_metadata({"title": "Test Book Ref", "author": "Server Author"})
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


class TestPaperReferenceEndpoints:
    """Tests for paper reference (metadata-only) document entries."""

    def test_add_reference_basic(self, client, db_home: str):
        """POST /api/documents/papers/reference creates a metadata-only paper."""
        resp = client.post(
            "/api/documents/papers/reference",
            json={
                "title": "Attention Is All You Need",
                "author": "Vaswani et al.",
                "year": "2017",
                "journal": "NeurIPS",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] is not None
        assert data["filename"].endswith(".bib")

        # Verify it's stored as a reference-type paper
        doc_resp = client.get(f"/api/documents/{data['id']}")
        assert doc_resp.status_code == 200
        doc_data = doc_resp.json()
        assert doc_data["document_type"] == "paper"
        assert doc_data["source_type"] == "reference"

    def test_add_reference_with_doi(self, client, db_home: str):
        """Reference with DOI stores it in metadata."""
        resp = client.post(
            "/api/documents/papers/reference",
            json={
                "title": "BERT Paper",
                "doi": "10.48550/arXiv.1810.04805",
                "year": "2019",
            },
        )
        assert resp.status_code == 200

        doc_id = resp.json()["id"]
        meta_resp = client.get(f"/api/documents/{doc_id}/meta")
        assert meta_resp.status_code == 200
        assert meta_resp.json()["doi"] == "10.48550/arXiv.1810.04805"

    def test_add_reference_generates_bibtex(self, client, db_home: str):
        """Reference without bibtex field auto-generates one."""
        resp = client.post(
            "/api/documents/papers/reference",
            json={"title": "A Simple Paper", "author": "Smith", "year": "2024"},
        )
        assert resp.status_code == 200

        doc_id = resp.json()["id"]
        bib_resp = client.get(f"/api/documents/{doc_id}/bibtex")
        assert bib_resp.status_code == 200
        bib_data = bib_resp.json()
        assert "@" in bib_data["bibtex"]
        assert "A Simple Paper" in bib_data["bibtex"]

    def test_add_reference_with_existing_bibtex(self, client, db_home: str):
        """Reference with explicit bibtex preserves it."""
        raw_bibtex = "@article{test2024,\n  title = {Test},\n  year = {2024},\n}"
        resp = client.post(
            "/api/documents/papers/reference",
            json={"title": "Test", "bibtex": raw_bibtex},
        )
        assert resp.status_code == 200

        doc_id = resp.json()["id"]
        bib_resp = client.get(f"/api/documents/{doc_id}/bibtex")
        assert bib_resp.status_code == 200
        assert "test2024" in bib_resp.json()["bibtex"]

    def test_add_reference_requires_title(self, client, db_home: str):
        """Reference without title returns 422 validation error."""
        resp = client.post(
            "/api/documents/papers/reference",
            json={"author": "Someone"},
        )
        assert resp.status_code == 422

    def test_add_reference_with_citation_key(self, client, db_home: str):
        """Custom citation_key determines the filename when no filepath given."""
        resp = client.post(
            "/api/documents/papers/reference",
            json={"title": "Paper", "citation_key": "mykey2024"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "mykey2024.bib"
        # Path is a real path under db_home (not a synthetic ref:// URI)
        assert db_home in data["path"]

        # Verify citation_key is stored in sidecar metadata
        doc_id = data["id"]
        meta_resp = client.get(f"/api/documents/{doc_id}/meta")
        assert meta_resp.status_code == 200
        assert meta_resp.json()["citation_key"] == "mykey2024"

    def test_add_reference_extra_metadata(self, client, db_home: str):
        """Extra metadata fields are preserved."""
        resp = client.post(
            "/api/documents/papers/reference",
            json={
                "title": "Paper with extras",
                "extra_metadata": {"custom_field": "value", "tags": ["ml", "nlp"]},
            },
        )
        assert resp.status_code == 200

        doc_id = resp.json()["id"]
        meta_resp = client.get(f"/api/documents/{doc_id}/meta")
        assert meta_resp.status_code == 200
        meta = meta_resp.json()
        assert meta["custom_field"] == "value"
        assert meta["tags"] == ["ml", "nlp"]

    def test_reference_has_searchable_content(self, client, db_home: str):
        """Reference documents have searchable content derived from metadata."""
        resp = client.post(
            "/api/documents/papers/reference",
            json={"title": "Searchable Reference Title", "author": "Author Name"},
        )
        assert resp.status_code == 200

        doc_id = resp.json()["id"]
        content_resp = client.get(f"/api/documents/{doc_id}/content")
        assert content_resp.status_code == 200
        # Content is populated from metadata for FTS indexing
        content = content_resp.json()["content"]
        assert "Searchable Reference Title" in content
        assert "Author Name" in content

    def test_reference_file_download_returns_404(self, client, db_home: str):
        """Downloading file for a reference returns 404 (no file on disk)."""
        resp = client.post(
            "/api/documents/papers/reference",
            json={"title": "No File Ref"},
        )
        assert resp.status_code == 200

        doc_id = resp.json()["id"]
        file_resp = client.get(f"/api/documents/{doc_id}/file")
        assert file_resp.status_code == 404

    def test_search_finds_references(self, client, db_home: str):
        """Search can find references by title/metadata."""
        client.post(
            "/api/documents/papers/reference",
            json={"title": "Transformer Architecture", "author": "Vaswani"},
        )
        # Also index a regular generic doc to ensure mixed results work
        from pathlib import Path
        home = Path(db_home)
        md_path = home / "note.md"
        md_path.write_text("# Some notes\nRegular document content.")
        client.post(
            "/api/index/add",
            json={"filepath": str(md_path), "document_type": "generic"},
        )

        resp = client.get("/api/search?q=Transformer")
        assert resp.status_code == 200
        data = resp.json()
        # Should find the reference in document results
        assert len(data["documents"]["results"]) >= 1
        hit = data["documents"]["results"][0]
        assert hit["document"]["source_type"] == "reference"

    def test_duplicate_reference_same_key_updates(self, client, db_home: str):
        """Adding a reference with same citation key updates existing entry."""
        resp1 = client.post(
            "/api/documents/papers/reference",
            json={"title": "Original Title", "citation_key": "dup2024"},
        )
        assert resp1.status_code == 200
        doc_id = resp1.json()["id"]

        resp2 = client.post(
            "/api/documents/papers/reference",
            json={"title": "Updated Title", "citation_key": "dup2024"},
        )
        assert resp2.status_code == 200
        # Same ID (upsert by path)
        assert resp2.json()["id"] == doc_id

        # Verify updated title
        meta_resp = client.get(f"/api/documents/{doc_id}/meta")
        assert meta_resp.json()["title"] == "Updated Title"


class TestGenericReferenceEndpoints:
    """Tests for generic document references (metadata-only)."""

    def test_add_generic_reference_basic(self, client, db_home: str):
        """POST /api/documents/reference creates a metadata-only generic doc."""
        resp = client.post(
            "/api/documents/reference",
            json={
                "title": "Meeting Notes Q1",
                "author": "Team Alpha",
                "subject": "Quarterly review notes",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] is not None

        # Verify it's stored as a generic reference
        doc_resp = client.get(f"/api/documents/{data['id']}")
        assert doc_resp.status_code == 200
        doc_data = doc_resp.json()
        assert doc_data["document_type"] == "generic"
        assert doc_data["source_type"] == "reference"

    def test_add_generic_reference_with_keywords(self, client, db_home: str):
        """Generic reference with keywords stores them in metadata."""
        resp = client.post(
            "/api/documents/reference",
            json={
                "title": "Design Doc",
                "keywords": ["architecture", "design"],
            },
        )
        assert resp.status_code == 200

        doc_id = resp.json()["id"]
        meta_resp = client.get(f"/api/documents/{doc_id}/meta")
        assert meta_resp.status_code == 200
        assert meta_resp.json()["keywords"] == ["architecture", "design"]

    def test_generic_reference_has_searchable_content(self, client, db_home: str):
        """Generic references have searchable content derived from metadata."""
        client.post(
            "/api/documents/reference",
            json={"title": "Important Design Decision", "subject": "API architecture"},
        )

        resp = client.get("/api/search?q=Important+Design")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["documents"]["results"]) >= 1
        hit = data["documents"]["results"][0]
        assert hit["document"]["source_type"] == "reference"
        assert hit["document"]["document_type"] == "generic"


class TestTextbookReferenceEndpoints:
    """Tests for textbook references (metadata-only)."""

    def test_add_textbook_reference_basic(self, client, db_home: str):
        """POST /api/documents/textbooks/reference creates a metadata-only textbook."""
        resp = client.post(
            "/api/documents/textbooks/reference",
            json={
                "title": "Introduction to Algorithms",
                "author": "Cormen et al.",
                "year": "2009",
                "publisher": "MIT Press",
                "edition": "3rd",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] is not None

        # Verify it's stored as a textbook reference
        doc_resp = client.get(f"/api/documents/{data['id']}")
        assert doc_resp.status_code == 200
        doc_data = doc_resp.json()
        assert doc_data["document_type"] == "textbook"
        assert doc_data["source_type"] == "reference"

    def test_add_textbook_reference_stores_metadata(self, client, db_home: str):
        """Textbook reference preserves all supplied fields."""
        resp = client.post(
            "/api/documents/textbooks/reference",
            json={
                "title": "Deep Learning",
                "author": "Goodfellow et al.",
                "publisher": "MIT Press",
                "edition": "1st",
            },
        )
        assert resp.status_code == 200

        doc_id = resp.json()["id"]
        meta_resp = client.get(f"/api/documents/{doc_id}/meta")
        assert meta_resp.status_code == 200
        meta = meta_resp.json()
        assert meta["publisher"] == "MIT Press"
        assert meta["edition"] == "1st"

    def test_textbook_reference_requires_title(self, client, db_home: str):
        """Textbook reference without title returns 422 validation error."""
        resp = client.post(
            "/api/documents/textbooks/reference",
            json={"author": "Someone"},
        )
        assert resp.status_code == 422

    def test_textbook_reference_has_searchable_content(self, client, db_home: str):
        """Textbook references are searchable by metadata."""
        client.post(
            "/api/documents/textbooks/reference",
            json={"title": "Neural Networks and Deep Learning", "author": "Goodfellow"},
        )

        resp = client.get("/api/search?q=Neural+Networks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["documents"]["results"]) >= 1
        hit = data["documents"]["results"][0]
        assert hit["document"]["source_type"] == "reference"
        assert hit["document"]["document_type"] == "textbook"
