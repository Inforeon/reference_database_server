from __future__ import annotations

"""Tests for document move functionality (Repository, Indexer, API, CLI)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docsearch.core.models import Document
from docsearch.core.repository import Repository
from docsearch.core.indexer import Indexer
from docsearch.server.app import create_app


# ── Helpers ────────────────────────────────────────────────────────


def _make_doc(path: str = "/tmp/test.md", **kwargs) -> Document:
    defaults = dict(
        path=path,
        filename=Path(path).name,
        directory=str(Path(path).parent),
        extension="md",
        size=100,
        mtime=1700000000.0,
        content_hash="abc123",
        extracted_metadata={"author": "Alice"},
        sidecar_metadata={"tags": ["test"]},
        full_text="hello world this is a test document",
    )
    defaults.update(kwargs)
    return Document(**defaults)


# ── Repository.rename tests ────────────────────────────────────────


class TestRepositoryRename:
    @pytest.fixture()
    def repo(self, tmp_path: Path):
        r = Repository(str(tmp_path / "test.db"))
        yield r
        r.close()

    def test_rename_updates_path_and_filename(self, repo: Repository):
        doc = _make_doc("/old/dir/file.md")
        repo.upsert(doc)
        old_id = repo.get("/old/dir/file.md").id

        assert repo.rename("/old/dir/file.md", "/new/dir/file.md")

        new_doc = repo.get("/new/dir/file.md")
        assert new_doc is not None
        assert new_doc.id == old_id  # id preserved
        assert new_doc.filename == "file.md"
        assert new_doc.directory == "/new/dir"

        # Old path no longer exists
        assert repo.get("/old/dir/file.md") is None

    def test_rename_preserves_all_other_fields(self, repo: Repository):
        doc = _make_doc(
            "/old/a.md",
            full_text="important content",
            extracted_metadata={"author": "Bob"},
            sidecar_metadata={"tags": ["x", "y"]},
        )
        repo.upsert(doc)

        repo.rename("/old/a.md", "/new/b.md")
        new_doc = repo.get("/new/b.md")
        assert new_doc.full_text == "important content"
        assert new_doc.extracted_metadata == {"author": "Bob"}
        assert new_doc.sidecar_metadata == {"tags": ["x", "y"]}
        assert new_doc.content_hash == "abc123"

    def test_rename_nonexistent_returns_false(self, repo: Repository):
        assert not repo.rename("/no/such/file.md", "/also/no.md")

    def test_rename_triggers_fts_update(self, repo: Repository):
        from docsearch.core.models import SearchQuery

        doc = _make_doc("/old/searchable.md", full_text="find me please")
        repo.upsert(doc)

        repo.rename("/old/searchable.md", "/new/searchable.md")

        # FTS must still find it after rename
        results = repo.search(SearchQuery(q="find me"))
        assert len(results) == 1
        assert results[0].document.path == "/new/searchable.md"


# ── Indexer.move_file tests ───────────────────────────────────────


class TestIndexerMoveFile:
    @pytest.fixture()
    def home(self, tmp_path: Path):
        home = tmp_path / "home"
        home.mkdir()
        return home

    @pytest.fixture()
    def repo(self, home: Path):
        r = Repository(str(home / "test.db"))
        yield r
        r.close()

    @pytest.fixture()
    def indexer(self, repo):
        return Indexer(repo)

    def _create_real_file(self, home: Path, name: str, content: str = "file content"):
        f = home / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        return f

    def test_move_file_on_disk_and_in_db(self, home: Path, indexer: Indexer):
        src = self._create_real_file(home, "src/doc.txt", "move me")
        doc = _make_doc(str(src), full_text="move me", extension="txt")
        indexer.repo.upsert(doc)
        # Fetch back from DB to get the assigned id
        old_id = indexer.repo.get(str(src)).id

        dst = home / "dst" / "doc.txt"
        result = indexer.move_file(str(src), str(dst))

        assert result is not None
        assert result.id == old_id
        assert result.path == str(dst)
        assert dst.is_file()
        assert not src.is_file()

    def test_move_creates_parent_directories(self, home: Path, indexer: Indexer):
        src = self._create_real_file(home, "top.txt", "nested")
        doc = _make_doc(str(src), full_text="nested", extension="txt")
        indexer.repo.upsert(doc)

        dst = home / "a" / "b" / "c" / "deep.txt"
        result = indexer.move_file(str(src), str(dst))

        assert result is not None
        assert dst.is_file()

    def test_move_preserves_sidecar(self, home: Path, indexer: Indexer):
        src = self._create_real_file(home, "meta.txt", "sidecar test")
        sidecar = Path(str(src) + ".meta.json")
        sidecar.write_text(json.dumps({"key": "val"}))
        doc = _make_doc(str(src), full_text="sidecar test", extension="txt")
        indexer.repo.upsert(doc)

        dst = home / "moved.txt"
        result = indexer.move_file(str(src), str(dst))

        assert result is not None
        new_sidecar = Path(str(dst) + ".meta.json")
        assert new_sidecar.is_file()
        assert json.loads(new_sidecar.read_text()) == {"key": "val"}
        assert not sidecar.is_file()

    def test_move_missing_source_returns_none(self, home: Path, indexer: Indexer):
        dst = home / "dst.txt"
        result = indexer.move_file("/nonexistent/file.txt", str(dst))
        assert result is None


# ── REST API endpoint tests ─────────────────────────────────────────


@pytest.fixture()
def api_home(tmp_path: Path):
    home = tmp_path / "api_home"
    home.mkdir(parents=True, exist_ok=True)
    return str(home)


@pytest.fixture()
def api_app(api_home: str):
    import os
    os.environ["DOCSEARCH_HOME"] = api_home
    _app = create_app()
    yield _app
    os.environ.pop("DOCSEARCH_HOME", None)


@pytest.fixture()
def api_client(api_app):
    return TestClient(api_app)


class TestMoveDocumentAPI:
    def _index_file(self, api_home: str, name: str, content: str = "content"):
        """Create a file within db home and index it via the API."""
        from pathlib import Path
        home = Path(api_home)
        f = home / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)

        import httpx
        with TestClient(create_app()) as c:
            resp = TestClient(api_app).__enter__().post(
                "/api/index/add",
                json={"filepath": str(f)},
            )
        return resp

    def test_move_to_relative_path(self, api_client, api_home: str):
        home = Path(api_home)
        f = home / "original.txt"
        f.write_text("move test content")

        # Index the file first
        resp = api_client.post(
            "/api/index/add",
            json={"filepath": str(f)},
        )
        assert resp.status_code == 200
        doc_id = resp.json()["id"]

        # Move to a subdirectory (relative destination)
        resp = api_client.post(
            f"/api/documents/{doc_id}/move",
            json={"destination": "subdir/moved.txt"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == doc_id  # ID preserved
        assert data["old_path"] == str(f)
        assert "subdir/moved.txt" in data["new_path"] or "subdir" in data["new_path"]
        assert (home / "subdir" / "moved.txt").is_file()

    def test_move_to_absolute_path(self, api_client, api_home: str):
        home = Path(api_home)
        f = home / "abs_test.txt"
        f.write_text("absolute move")

        resp = api_client.post(
            "/api/index/add",
            json={"filepath": str(f)},
        )
        doc_id = resp.json()["id"]

        dst = home / "dest" / "renamed.txt"
        resp = api_client.post(
            f"/api/documents/{doc_id}/move",
            json={"destination": str(dst)},
        )
        assert resp.status_code == 200
        assert dst.is_file()

    def test_move_rejects_destination_outside_home(self, api_client, api_home: str):
        home = Path(api_home)
        f = home / "out.txt"
        f.write_text("traversal attempt")

        resp = api_client.post(
            "/api/index/add",
            json={"filepath": str(f)},
        )
        doc_id = resp.json()["id"]

        resp = api_client.post(
            f"/api/documents/{doc_id}/move",
            json={"destination": "../../etc/evil.txt"},
        )
        assert resp.status_code == 400

    def test_move_404_missing_document(self, api_client, api_home: str):
        # Use a destination inside the home so the containment check passes
        resp = api_client.post(
            "/api/documents/9999/move",
            json={"destination": "within_home.txt"},
        )
        assert resp.status_code == 404

    def test_move_preserves_document_id(self, api_client, api_home: str):
        """Ensure the internal DB id stays the same after a move."""
        home = Path(api_home)
        f = home / "keep_id.txt"
        f.write_text("identity check")

        resp = api_client.post(
            "/api/index/add",
            json={"filepath": str(f)},
        )
        original_id = resp.json()["id"]

        resp = api_client.post(
            f"/api/documents/{original_id}/move",
            json={"destination": "new_place.txt"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == original_id

        # Verify we can still fetch by the same id
        resp2 = api_client.get(f"/api/documents/{original_id}")
        assert resp2.status_code == 200
        assert resp2.json()["filename"] == "new_place.txt"
