from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from docsearch.core.models import SearchQuery
from docsearch.core.repository import Repository
from docsearch.server.dependencies import get_config
from docsearch.server.schemas import (
    ChapterResponse,
    ChapterSearchGroup,
    ChapterSearchResultResponse,
    CompactChapterHit,
    CompactChapterSearchGroup,
    CompactDocumentHit,
    CompactDocumentSearchGroup,
    CompactSearchResponse,
    DocumentResponse,
    DocumentSearchGroup,
    SearchResponse,
    SearchResultResponse,
    _extract_title,
    _extract_year,
    _format_author_slim,
    _format_pages,
)

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchResponse | CompactSearchResponse)
async def search(
    q: str = Query("", description="Full-text search query"),
    scope: str = Query("", description="Restrict to subdirectory prefix"),
    file_type: str = Query("", description="Filter by extension"),
    author: str = Query("", description="Filter by author"),
    tags: str = Query("", description="Comma-separated tags"),
    after: str = Query("", description="Modified after (ISO date)"),
    before: str = Query("", description="Modified before (ISO date)"),
    document_types: str = Query("", description="Comma-separated document types to include"),
    raw_fts: bool = Query(False, description="Pass q to FTS5 verbatim instead of as plain text"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    verbose: bool = Query(False, description="Include full metadata in results (default: compact)"),
    config = Depends(get_config),
) -> SearchResponse | CompactSearchResponse:
    """Search indexed documents and textbook chapters.

    Returns separated result groups: ``documents`` for generic/paper/textbook
    document-level results, ``chapters`` for textbook chapter results.
    Use ``document_types`` to filter which document types participate.
    ``q`` is plain text by default — FTS5 operator characters (``-``, ``(``,
    ``:``, ``*``) are searched for rather than parsed; set ``raw_fts`` to opt
    into FTS5's own query syntax.

    By default, returns compact results with slim metadata. Set ``verbose=true``
    for full document metadata (current behaviour).
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    type_list = [t.strip() for t in document_types.split(",") if t.strip()] if document_types else []

    sq = SearchQuery(
        q=q,
        scope=scope,
        file_type=file_type,
        author=author,
        tags=tag_list,
        after=after,
        before=before,
        document_types=type_list,
        raw_fts=raw_fts,
        offset=offset,
        limit=limit,
    )

    repo = Repository(str(config.db_path), config.home)
    try:
        # ── Phase 1: Non-textbook documents (generic, paper) ──────
        doc_results: list[SearchResultResponse] = []
        compact_doc_results: list[CompactDocumentHit] = []
        non_textbook_types = ["generic", "paper"]
        if any(sq.includes_type(t) for t in non_textbook_types):
            filtered_sq = SearchQuery(**sq.__dict__)
            filtered_sq.document_types = [
                t for t in non_textbook_types if sq.includes_type(t)
            ] or non_textbook_types
            raw_docs = repo.search(filtered_sq)
            for r in raw_docs:
                doc_results.append(_to_search_response(r))
                compact_doc_results.append(_to_compact_hit(r))

        # ── Phase 2: Textbook documents (title/metadata level) ──────
        if sq.includes_type("textbook"):
            tb_sq = SearchQuery(**sq.__dict__)
            tb_sq.document_types = ["textbook"]
            raw_tb_docs = repo.search(tb_sq)
            for r in raw_tb_docs:
                doc_results.append(_to_search_response(r))
                compact_doc_results.append(_to_compact_hit(r))

        # ── Phase 3: Textbook chapters (full_text + title level) ────
        chap_results: list[ChapterSearchResultResponse] = []
        compact_chap_results: list[CompactChapterHit] = []
        if sq.includes_type("textbook"):
            raw_chaps = repo.search_textbook_chapters(sq)
            for r in raw_chaps:
                chap_resp = ChapterResponse(
                    id=r.chapter.id,
                    textbook_id=r.chapter.textbook_id,
                    chapter_index=r.chapter.chapter_index,
                    title=r.chapter.title,
                    chapter_type=r.chapter.chapter_type or "range",
                    start_page=r.chapter.start_page,
                    end_page=r.chapter.end_page,
                    page_count=r.chapter.page_count,
                    file_path=r.chapter.file_path,
                    metadata=r.chapter.combined_metadata(r.document),
                )
                doc_resp = DocumentResponse(
                    id=r.document.id,
                    path=r.document.path,
                    filename=r.document.filename,
                    directory=r.document.directory,
                    extension=r.document.extension,
                    document_type=r.document.document_type,
                    source_type=r.document.source_type,
                    size=r.document.size,
                    mtime=r.document.mtime,
                    metadata=r.document.combined_metadata,
                    indexed_at=r.document.indexed_at,
                )
                chap_results.append(
                    ChapterSearchResultResponse(
                        chapter=chap_resp,
                        parent_document=doc_resp,
                        score=r.score,
                    )
                )
                compact_chap_results.append(
                    CompactChapterHit(
                        chapter_id=r.chapter.id,
                        textbook_id=r.chapter.textbook_id,
                        chapter_index=r.chapter.chapter_index,
                        title=r.chapter.title,
                        pages=_format_pages(r.chapter.start_page, r.chapter.end_page),
                        score=r.score,
                    )
                )

        if verbose:
            return SearchResponse(
                documents=DocumentSearchGroup(results=doc_results, total=len(doc_results)),
                chapters=ChapterSearchGroup(results=chap_results, total=len(chap_results)),
            )
        return CompactSearchResponse(
            documents=CompactDocumentSearchGroup(results=compact_doc_results, total=len(compact_doc_results)),
            chapters=CompactChapterSearchGroup(results=compact_chap_results, total=len(compact_chap_results)),
        )
    finally:
        repo.close()


def _to_search_response(r) -> SearchResultResponse:
    d = r.document
    return SearchResultResponse(
        document=DocumentResponse(
            id=d.id,
            path=d.path,
            filename=d.filename,
            directory=d.directory,
            extension=d.extension,
            document_type=d.document_type,
            source_type=d.source_type,
            size=d.size,
            mtime=d.mtime,
            metadata=d.combined_metadata,
            indexed_at=d.indexed_at,
        ),
        score=r.score,
        snippet=r.snippet,
    )


def _to_compact_hit(r) -> CompactDocumentHit:
    d = r.document
    meta = d.combined_metadata
    return CompactDocumentHit(
        id=d.id,
        path=d.path,
        document_type=d.document_type,
        title=_extract_title(meta),
        author=_format_author_slim(meta),
        year=_extract_year(meta),
        score=r.score,
    )
