from __future__ import annotations

"""Tests for metadata edits staying coherent across the DB column and sidecar file.

A metadata key lives in two places: ``documents.sidecar_metadata`` (what search,
tag filters and every read path use) and ``<path>.meta.json`` (what re-indexing
reads back).  An edit applied to only one of them is either invisible or lost on
the next scan.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from docsearch.cli.main import cli
from docsearch.core.indexer import Indexer
from docsearch.core.repository import Repository


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    h = tmp_path / "db_home"
    h.mkdir()
    return h


@pytest.fixture()
def repo(home: Path):
    r = Repository(str(home / "docsearch.db"), home)
    yield r
    r.close()


@pytest.fixture()
def indexer(repo: Repository, home: Path) -> Indexer:
    return Indexer(repo, home)


def _index_md(indexer: Indexer, repo: Repository, rel: str, text: str = "content") -> int:
    p = indexer.home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    doc = indexer.add_file(rel)
    assert doc is not None and doc.id is not None
    return doc.id


class TestIndexerMetadataEdits:
    def test_set_key_updates_db_and_file(self, indexer: Indexer, repo: Repository):
        doc_id = _index_md(indexer, repo, "papers/a.md")

        assert indexer.set_metadata_key(doc_id, "arxiv_id", "1706.03762")

        doc = repo.get_by_id(doc_id)
        assert doc.sidecar_metadata["arxiv_id"] == "1706.03762"

        sidecar = indexer.metadata_sidecar_path(doc)
        assert sidecar.name == "a.md.meta.json"
        assert json.loads(sidecar.read_text())["arxiv_id"] == "1706.03762"

    def test_set_key_preserves_existing_keys(self, indexer: Indexer, repo: Repository):
        doc_id = _index_md(indexer, repo, "papers/b.md")
        indexer.set_metadata_key(doc_id, "tags", ["ml"])

        indexer.set_metadata_key(doc_id, "status", "read")

        stored = repo.get_by_id(doc_id).sidecar_metadata
        assert stored["tags"] == ["ml"]
        assert stored["status"] == "read"

    def test_delete_key_removes_from_both(self, indexer: Indexer, repo: Repository):
        doc_id = _index_md(indexer, repo, "papers/c.md")
        indexer.set_metadata_key(doc_id, "temp", 1)

        assert indexer.delete_metadata_key(doc_id, "temp")

        assert "temp" not in repo.get_by_id(doc_id).sidecar_metadata
        sidecar = indexer.metadata_sidecar_path(repo.get_by_id(doc_id))
        assert "temp" not in json.loads(sidecar.read_text())

    def test_delete_absent_key_is_not_an_error(self, indexer: Indexer, repo: Repository):
        doc_id = _index_md(indexer, repo, "papers/d.md")
        assert indexer.delete_metadata_key(doc_id, "never_set") is True

    def test_unknown_document_returns_false(self, indexer: Indexer):
        assert indexer.set_metadata_key(9999, "k", "v") is False
        assert indexer.delete_metadata_key(9999, "k") is False

    def test_edit_survives_reindex(self, indexer: Indexer, repo: Repository):
        """The sidecar file is what re-indexing reads back — the edit must persist."""
        doc_id = _index_md(indexer, repo, "papers/e.md")
        indexer.set_metadata_key(doc_id, "arxiv_id", "2502.05171")

        assert indexer.add_file("papers/e.md") is not None

        assert repo.get_by_id(doc_id).sidecar_metadata["arxiv_id"] == "2502.05171"

    def test_hand_edited_sidecar_is_not_discarded(self, indexer: Indexer, repo: Repository):
        """Hand-edited .meta.json is a documented workflow; editing another key keeps it."""
        doc_id = _index_md(indexer, repo, "papers/f.md")
        sidecar = indexer.metadata_sidecar_path(repo.get_by_id(doc_id))
        sidecar.write_text(json.dumps({"hand": "written"}))

        indexer.set_metadata_key(doc_id, "status", "read")

        stored = repo.get_by_id(doc_id).sidecar_metadata
        assert stored == {"hand": "written", "status": "read"}

    def test_no_re_extraction_on_edit(self, indexer: Indexer, repo: Repository, monkeypatch: pytest.MonkeyPatch):
        """A one-key edit must not re-read or re-extract the document."""
        doc_id = _index_md(indexer, repo, "papers/g.md")
        before = repo.get_by_id(doc_id)

        def explode(*args, **kwargs):
            raise AssertionError("metadata edit must not re-extract the document")

        monkeypatch.setattr(Indexer, "add_file", explode)
        assert indexer.set_metadata_key(doc_id, "k", "v") is True

        after = repo.get_by_id(doc_id)
        assert after.content_hash == before.content_hash
        assert after.full_text == before.full_text

    def test_reference_entry_gets_a_sidecar(self, indexer: Indexer, repo: Repository):
        """Reference-only entries have no file, but their sidecar is still written."""
        doc = indexer.add_reference("refs/smith2024.bib", document_type="paper", extra_metadata={
            "title": "A Study", "author": "Smith", "year": "2024",
        })
        assert doc is not None
        stored = repo.get_by_id(doc.id)
        assert stored.source_type == "reference"
        assert not (indexer.home / stored.path).exists()

        assert indexer.set_metadata_key(stored.id, "arxiv_id", "1706.03762")

        sidecar = indexer.metadata_sidecar_path(stored)
        assert sidecar.name == "smith2024.bib.meta.json"
        assert json.loads(sidecar.read_text())["arxiv_id"] == "1706.03762"
        assert repo.get_by_id(stored.id).sidecar_metadata["arxiv_id"] == "1706.03762"

    def test_directory_textbook_sidecar_lives_inside(self, indexer: Indexer, repo: Repository):
        """Directory-type textbooks keep their sidecar at <dir>/<dirname>.meta.json."""
        from docsearch.core.models import Document

        book = indexer.home / "library" / "mybook"
        book.mkdir(parents=True)
        repo.upsert(
            Document(
                path="library/mybook",
                filename="mybook",
                directory="library",
                extension="",
                document_type="textbook",
                source_type="directory",
                sidecar_metadata={"title": "My Book"},
            )
        )
        stored = repo.get("library/mybook")

        assert indexer.set_metadata_key(stored.id, "tags", ["read"])

        expected = book / "mybook.meta.json"
        assert expected.is_file()
        assert json.loads(expected.read_text())["tags"] == ["read"]
        assert not (indexer.home / "library" / "mybook.meta.json").exists()


class TestRepositoryUpdateSidecar:
    def test_patch_merges(self, repo: Repository, home: Path):
        from docsearch.core.models import Document

        repo.upsert(
            Document(path="a.md", filename="a.md", directory="", extension="md",
                     sidecar_metadata={"keep": 1})
        )
        doc_id = repo.get("a.md").id

        assert repo.update_sidecar_metadata(doc_id, patch={"add": 2})

        assert repo.get_by_id(doc_id).sidecar_metadata == {"keep": 1, "add": 2}

    def test_remove_keys(self, repo: Repository):
        from docsearch.core.models import Document

        repo.upsert(
            Document(path="b.md", filename="b.md", directory="", extension="md",
                     sidecar_metadata={"drop": 1, "keep": 2})
        )
        doc_id = repo.get("b.md").id

        repo.update_sidecar_metadata(doc_id, remove_keys=["drop"])

        assert repo.get_by_id(doc_id).sidecar_metadata == {"keep": 2}

    def test_unknown_id_returns_false(self, repo: Repository):
        assert repo.update_sidecar_metadata(4242, patch={"a": 1}) is False

    def test_corrupt_column_is_replaced_not_raised(self, repo: Repository, home: Path):
        from docsearch.core.models import Document

        repo.upsert(Document(path="c.md", filename="c.md", directory="", extension="md"))
        doc_id = repo.get("c.md").id
        with repo._conn:  # simulate a damaged row
            repo._conn.execute(
                "UPDATE documents SET sidecar_metadata = 'not json' WHERE id = ?", (doc_id,)
            )

        assert repo.update_sidecar_metadata(doc_id, patch={"fixed": True})
        assert repo.get_by_id(doc_id).sidecar_metadata == {"fixed": True}


class TestCliMeta:
    @pytest.fixture()
    def runner(self) -> CliRunner:
        return CliRunner()

    def _invoke(self, runner: CliRunner, home: Path, *args: str):
        return runner.invoke(cli, ["--home", str(home), *args])

    def test_set_then_show_reports_stored_value(
        self, runner: CliRunner, home: Path, indexer: Indexer, repo: Repository
    ):
        _index_md(indexer, repo, "papers/h.md")

        result = self._invoke(
            runner, home, "meta", "set", str(home / "papers" / "h.md"), "-k", "arxiv_id", "-v", '"1706.03762"'
        )
        assert result.exit_code == 0, result.output

        show = self._invoke(runner, home, "meta", "show", str(home / "papers" / "h.md"))
        assert json.loads(show.output)["arxiv_id"] == "1706.03762"

    def test_set_writes_db_column_not_just_file(
        self, runner: CliRunner, home: Path, indexer: Indexer, repo: Repository
    ):
        doc_id = _index_md(indexer, repo, "papers/i.md")

        self._invoke(
            runner, home, "meta", "set", str(home / "papers" / "i.md"), "-k", "status", "-v", "read"
        )

        assert repo.get_by_id(doc_id).sidecar_metadata["status"] == "read"

    def test_delete_key(
        self, runner: CliRunner, home: Path, indexer: Indexer, repo: Repository
    ):
        doc_id = _index_md(indexer, repo, "papers/j.md")
        indexer.set_metadata_key(doc_id, "temp", "x")

        result = self._invoke(
            runner, home, "meta", "delete", str(home / "papers" / "j.md"), "-k", "temp"
        )
        assert result.exit_code == 0, result.output
        assert "temp" not in repo.get_by_id(doc_id).sidecar_metadata

    def test_set_on_unindexed_path_errors(
        self, runner: CliRunner, home: Path
    ):
        stray = home / "stray.md"
        stray.write_text("nothing indexed")

        result = self._invoke(runner, home, "meta", "set", str(stray), "-k", "a", "-v", "b")

        assert result.exit_code != 0
        assert "not an indexed document" in result.output

    def test_show_falls_back_to_file_when_not_indexed(
        self, runner: CliRunner, home: Path
    ):
        stray = home / "loose.md"
        stray.write_text("x")
        (home / "loose.md.meta.json").write_text(json.dumps({"hand": "made"}))

        result = self._invoke(runner, home, "meta", "show", str(stray))

        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {"hand": "made"}

    def test_set_works_from_a_subdirectory(
        self, runner: CliRunner, home: Path, indexer: Indexer, repo: Repository, monkeypatch: pytest.MonkeyPatch
    ):
        doc_id = _index_md(indexer, repo, "papers/sub/k.md")
        monkeypatch.chdir(home / "papers" / "sub")

        result = self._invoke(runner, home, "meta", "set", "k.md", "-k", "tag", "-v", '"x"')

        assert result.exit_code == 0, result.output
        assert repo.get_by_id(doc_id).sidecar_metadata["tag"] == "x"


class TestCliMetaPathsAndKeyFilter:
    """Round-2 bugs: paths must resolve against home from any cwd and fail loudly;
    ``show`` needs a ``-k`` filter so author-heavy records stay readable."""

    @pytest.fixture()
    def runner(self) -> CliRunner:
        return CliRunner()

    def _invoke(self, runner: CliRunner, home: Path, *args: str):
        return runner.invoke(cli, ["--home", str(home), *args])

    def test_show_resolves_home_relative_path_from_elsewhere(
        self, runner: CliRunner, home: Path, indexer: Indexer, repo: Repository,
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ):
        doc_id = _index_md(indexer, repo, "papers/deep/a.md")
        indexer.set_metadata_key(doc_id, "status", "read")
        monkeypatch.chdir(tmp_path)  # cwd is now outside the database home

        result = self._invoke(runner, home, "meta", "show", "papers/deep/a.md")

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["status"] == "read"

    def test_show_absolute_path_still_works(
        self, runner: CliRunner, home: Path, indexer: Indexer, repo: Repository
    ):
        doc_id = _index_md(indexer, repo, "papers/abs.md")
        indexer.set_metadata_key(doc_id, "status", "x")

        result = self._invoke(runner, home, "meta", "show", str(home / "papers" / "abs.md"))

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["status"] == "x"

    def test_show_unknown_path_errors_loudly(self, runner: CliRunner, home: Path):
        """A mistyped or wrongly-rooted path must not look like 'no metadata'."""
        result = self._invoke(runner, home, "meta", "show", "nope/missing.md")

        assert result.exit_code != 0
        assert "neither an indexed document" in result.output

    def test_show_key_filter_prints_string_bare(
        self, runner: CliRunner, home: Path, indexer: Indexer, repo: Repository
    ):
        doc_id = _index_md(indexer, repo, "papers/k1.md")
        indexer.set_metadata_key(doc_id, "arxiv_id", "1706.03762")

        result = self._invoke(runner, home, "meta", "show", str(home / "papers" / "k1.md"), "-k", "arxiv_id")

        assert result.exit_code == 0, result.output
        assert result.output.strip() == "1706.03762"

    def test_show_key_filter_prints_structured_as_json(
        self, runner: CliRunner, home: Path, indexer: Indexer, repo: Repository
    ):
        doc_id = _index_md(indexer, repo, "papers/k2.md")
        indexer.set_metadata_key(doc_id, "tags", ["ml", "rl"])

        result = self._invoke(runner, home, "meta", "show", str(home / "papers" / "k2.md"), "-k", "tags")

        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == ["ml", "rl"]

    def test_show_missing_key_lists_available(
        self, runner: CliRunner, home: Path, indexer: Indexer, repo: Repository
    ):
        doc_id = _index_md(indexer, repo, "papers/k3.md")
        indexer.set_metadata_key(doc_id, "status", "read")

        result = self._invoke(runner, home, "meta", "show", str(home / "papers" / "k3.md"), "-k", "absent")

        assert result.exit_code != 0
        assert "status" in result.output  # the available keys are named

    def test_init_does_not_clobber_existing_sidecar(
        self, runner: CliRunner, home: Path, indexer: Indexer, repo: Repository
    ):
        doc_id = _index_md(indexer, repo, "papers/i.md")
        indexer.set_metadata_key(doc_id, "keep", "me")
        sidecar = indexer.metadata_sidecar_path(repo.get_by_id(doc_id))

        result = self._invoke(runner, home, "meta", "init", str(home / "papers" / "i.md"))

        assert result.exit_code == 0, result.output
        assert json.loads(sidecar.read_text()) == {"keep": "me"}

    def test_init_missing_file_errors(self, runner: CliRunner, home: Path):
        result = self._invoke(runner, home, "meta", "init", str(home / "ghost.md"))

        assert result.exit_code != 0


class TestCliMetaValueQuoting:
    """`-v` keeps the JSON-first convention; quoting is how you force a string."""

    @pytest.fixture()
    def runner(self) -> CliRunner:
        return CliRunner()

    def test_bare_number_is_a_number(self, indexer: Indexer, repo: Repository):
        from docsearch.cli.utils import parse_meta_value

        assert parse_meta_value("2018") == 2018
        assert isinstance(parse_meta_value("2018"), int)

    def test_quoted_number_is_a_string(self, indexer: Indexer, repo: Repository):
        from docsearch.cli.utils import parse_meta_value

        assert parse_meta_value('"1706.03762"') == "1706.03762"

    def test_unquoted_identifier_number_becomes_a_float(self):
        """Documents the lossiness the quoting convention exists to avoid."""
        from docsearch.cli.utils import parse_meta_value

        assert parse_meta_value("1710.04820") == 1710.0482  # trailing zero gone


class TestParseMetaPairs:
    """Tests for parse_meta_pairs() — malformed pairs abort rather than skip."""

    def test_valid_pairs_parse(self):
        from docsearch.cli.utils import parse_meta_pairs

        result = parse_meta_pairs(("key1=value1", "key2=42"))
        assert result == {"key1": "value1", "key2": 42}

    def test_empty_pairs_returns_none(self):
        from docsearch.cli.utils import parse_meta_pairs

        assert parse_meta_pairs(()) is None

    def test_malformed_pair_raises(self):
        from docsearch.cli.utils import parse_meta_pairs
        import click

        with pytest.raises(click.ClickException, match="Invalid metadata pair"):
            parse_meta_pairs(("no_equals_sign",))

    def test_malformed_pair_aborts_before_good_ones(self):
        """A bad pair in the middle must abort — no partial collection."""
        from docsearch.cli.utils import parse_meta_pairs
        import click

        with pytest.raises(click.ClickException, match="Invalid metadata pair"):
            parse_meta_pairs(("good=1", "bad_pair", "also_good=2"))

    def test_value_with_equals_sign(self):
        """Value may contain further = characters."""
        from docsearch.cli.utils import parse_meta_pairs

        result = parse_meta_pairs(("equation=a=b+c",))
        assert result == {"equation": "a=b+c"}


class TestPapersAddMetaAbort:
    """CLI integration: papers add must abort on malformed -m before indexing."""

    def test_invalid_meta_aborts_without_indexing(self, tmp_path):
        import fitz
        import click.testing

        from docsearch.cli.main import cli
        from docsearch.core.repository import Repository

        # Create a minimal PDF
        pdf = fitz.open()
        pdf.new_page()
        pdf_path = tmp_path / "test.pdf"
        pdf.save(str(pdf_path))
        pdf.close()

        runner = click.testing.CliRunner()
        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["--home", str(tmp_path), "papers", "add", str(pdf_path), "--skip-bib", "-m", "bad_pair"],
                catch_exceptions=False,
            )

        assert result.exit_code != 0
        assert "Invalid metadata pair" in result.output

        # Verify nothing was indexed
        repo = Repository(str(tmp_path / "docsearch.db"))
        assert repo.count() == 0
        repo.close()
