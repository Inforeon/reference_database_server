from __future__ import annotations

"""Tests for search-query compilation and the metadata filters (round 2 bugs).

Two independent defects lived in ``Repository.search`` /
``search_textbook_chapters``:

* the full-text query was handed to FTS5 raw, so ordinary terms containing
  operator characters (``actor-critic``, ``model (based)``) raised instead of
  matching;
* the author filter read only ``extracted_metadata`` with exact equality, so it
  could not see curated authors nor match one name within a list.
"""

import pytest

from docsearch.core.models import Chapter, Document, SearchQuery
from docsearch.core.repository import Repository, fts_match_query


@pytest.fixture()
def repo(tmp_path):
    r = Repository(str(tmp_path / "test.db"))
    yield r
    r.close()


def _add(repo, path, *, text="", extracted=None, sidecar=None, doc_type="generic"):
    from pathlib import Path as P

    doc = Document(
        path=path,
        filename=P(path).name,
        directory=str(P(path).parent),
        extension="pdf",
        document_type=doc_type,
        extracted_metadata=extracted or {},
        sidecar_metadata=sidecar or {},
        full_text=text,
    )
    doc.id = repo.upsert(doc)
    return doc


def _search(repo, **kwargs):
    return repo.search(SearchQuery(**kwargs))


class TestFtsMatchQuery:
    """Unit tests for the free-text → FTS5 compiler."""

    def test_plain_terms_become_quoted_and(self):
        assert fts_match_query("actor critic") == '"actor" AND "critic"'

    def test_hyphen_survives_as_text(self):
        assert fts_match_query("actor-critic") == '"actor-critic"'

    def test_embedded_quote_is_doubled(self):
        assert fts_match_query('say "hi"') == '"say" AND """hi"""'

    def test_trailing_star_stays_a_prefix_operator(self):
        assert fts_match_query("model*") == '"model"*'

    def test_interior_star_is_neutralised(self):
        # Only a trailing * is meaningful; elsewhere it is ordinary text.
        assert fts_match_query("a*b") == '"a*b"'

    def test_punctuation_only_term_becomes_an_empty_phrase(self):
        # "-" compiles to a phrase whose tokens are all stripped → matches nothing,
        # but stays a *valid* FTS5 expression rather than a syntax error.
        assert fts_match_query("-") == '"-"'

    def test_no_usable_term_matches_nothing(self):
        # All-star or blank input has no term at all; compile to a literal that
        # hits zero, since FTS5 rejects a genuinely empty expression.
        assert fts_match_query("*") == '""'
        assert fts_match_query("   ") == '""'


class TestFtsOperatorCharacters:
    """The reported crashes: operator characters must be searched, not parsed."""

    @pytest.fixture(autouse=True)
    def corpus(self, repo):
        _add(repo, "a.pdf", text="The actor-critic architecture in RL")
        _add(repo, "b.pdf", text="neuroplasticity and plasticity in models")
        _add(repo, "c.pdf", text="a model based approach to models")

    @pytest.mark.parametrize("term", ["actor-critic", "-plasticity", "model (based)", "few-shot"])
    def test_operator_terms_do_not_crash(self, repo, term):
        # Should return a result list (possibly empty), never raise.
        _search(repo, q=term)

    def test_hyphenated_term_matches_its_document(self, repo):
        ids = [r.document.path for r in _search(repo, q="actor-critic")]
        assert ids == ["a.pdf"]

    def test_leading_hyphen_searches_the_word(self, repo):
        ids = [r.document.path for r in _search(repo, q="-plasticity")]
        assert "b.pdf" in ids

    def test_parentheses_are_literal(self, repo):
        ids = [r.document.path for r in _search(repo, q="model (based)")]
        assert ids == ["c.pdf"]

    def test_prefix_operator_still_works(self, repo):
        ids = [r.document.path for r in _search(repo, q="model*")]
        # "models" and "model" both reachable via prefix; a.pdf has neither.
        assert set(ids) == {"b.pdf", "c.pdf"}

    def test_degenerate_query_returns_nothing_without_error(self, repo):
        # "-" and "*" compile to valid-but-empty FTS expressions. (An empty q is
        # a browse — it returns everything — so it is not exercised here.)
        assert _search(repo, q="-") == []
        assert _search(repo, q="*") == []

    def test_raw_fts_passes_syntax_through(self, repo):
        """With raw_fts the caller gets FTS5's own language, incl. OR."""
        _add(repo, "d.pdf", text="alpha only here")
        ids = {r.document.path for r in _search(repo, q="actor OR alpha", raw_fts=True)}
        assert ids == {"a.pdf", "d.pdf"}

    def test_default_treats_or_as_a_word(self, repo):
        _add(repo, "e.pdf", text="alpha only here")
        # Without raw_fts, "OR" is just another required term → nothing has all three.
        assert _search(repo, q="actor OR alpha") == []


class TestChapterFtsRobustness:
    def test_chapter_search_survives_operator_chars(self, repo):
        book = _add(repo, "book.pdf", text="textbook", doc_type="textbook")
        repo.upsert_chapter(
            Chapter(textbook_id=book.id, chapter_index=0, title="actor-critic methods",
                    full_text="deep dive into actor-critic and self-supervised learning")
        )
        results = repo.search_textbook_chapters(SearchQuery(q="actor-critic"))
        assert len(results) == 1
        assert results[0].chapter.title == "actor-critic methods"


class TestAuthorFilter:
    @pytest.fixture(autouse=True)
    def corpus(self, repo):
        # sidecar list (curated), extracted empty — the reported dead case
        _add(repo, "schulman.pdf", sidecar={"author": ["John Schulman", "Daniil Arpino"]})
        # extracted only — fallback path
        _add(repo, "silver.pdf", extracted={"author": "David Silver"})
        # sidecar overrides extracted: extracted is wrong, curated is right
        _add(repo, "override.pdf", extracted={"author": "Bogus Pdf Author"},
             sidecar={"author": ["Volodymyr Mnih", "Koray Kavukcuoglu"]})
        # "A and B" string shape
        _add(repo, "and.pdf", sidecar={"author": "Ada Lovelace and Grace Hopper"})
        # pdf2bib dict shape under authors_bib
        _add(repo, "dict.pdf", sidecar={"authors_bib": [
            {"given": "John", "family": "Schulman", "sequence": "first"}]})

    def test_sidecar_list_matches_contained_name(self, repo):
        ids = {r.document.path for r in _search(repo, author="David Silver")}
        assert "silver.pdf" in ids

    def test_partial_surname_matches(self, repo):
        ids = {r.document.path for r in _search(repo, author="Schulman")}
        assert {"schulman.pdf", "dict.pdf"} <= ids

    def test_name_order_is_forgiven(self, repo):
        ids = {r.document.path for r in _search(repo, author="Schulman John")}
        assert {"schulman.pdf", "dict.pdf"} <= ids

    def test_and_string_shape_matches_either_author(self, repo):
        assert {r.document.path for r in _search(repo, author="Grace Hopper")} == {"and.pdf"}

    def test_curated_sidecar_overrides_wrong_extracted(self, repo):
        assert {r.document.path for r in _search(repo, author="Volodymyr Mnih")} == {"override.pdf"}
        # the stale PDF author must not resurrect a match once the sidecar disagrees
        assert _search(repo, author="Bogus Pdf Author") == []

    def test_unknown_author_matches_nothing(self, repo):
        assert _search(repo, author="Nobody Here") == []

    def test_corrupt_metadata_does_not_raise(self, repo):
        import sqlite3

        _add(repo, "corrupt.pdf", sidecar={"author": ["Fine Person"]})
        with repo._conn:
            repo._conn.execute(
                "UPDATE documents SET extracted_metadata='not json' WHERE path='corrupt.pdf'"
            )
        # A damaged row must not poison the whole query.
        assert {r.document.path for r in _search(repo, author="Fine Person")} == {"corrupt.pdf"}


class TestTextbookAuthorFilter:
    """Chapter search resolves textbooks through _resolve_textbook_ids — same filter."""

    def test_chapter_search_honours_curated_author(self, repo):
        book = _add(repo, "book.pdf", text="the book", doc_type="textbook",
                    sidecar={"author": ["John Schulman"]})
        repo.upsert_chapter(
            Chapter(textbook_id=book.id, chapter_index=0, title="Policy Gradients",
                    full_text="proving the policy gradient theorem")
        )
        hits = repo.search_textbook_chapters(SearchQuery(author="Schulman"))
        assert [r.chapter.title for r in hits] == ["Policy Gradients"]

        assert repo.search_textbook_chapters(SearchQuery(author="Nobody")) == []
