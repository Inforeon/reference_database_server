from __future__ import annotations

import os
from fastapi import APIRouter, Depends, Query

from docsearch.core.models import SearchQuery
from docsearch.core.repository import Repository
from docsearch.server.schemas import (
    DocumentResponse,
    SearchResultResponse,
)

router = APIRouter(prefix="/api/search", tags=["search"])


def get_db_path() -> str:
    return os.environ.get("DOCSEARCH_DB", os.path.expanduser("~/.local/share/docsearch/docsearch.db"))


@router.get("", response_model=list[SearchResultResponse])
async def search(
    q: str = Query("", description="Full-text search query"),
    scope: str = Query("", description="Restrict to subdirectory prefix"),
    file_type: str = Query("", description="Filter by extension"),
    author: str = Query("", description="Filter by author"),
    tags: str = Query("", description="Comma-separated tags"),
    after: str = Query("", description="Modified after (ISO date)"),
    before: str = Query("", description="Modified before (ISO date)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db_path: str = Depends(get_db_path),
) -> list[SearchResultResponse]:
    """Search indexed documents."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    sq = SearchQuery(
        q=q,
        scope=scope,
        file_type=file_type,
        author=author,
        tags=tag_list,
        after=after,
        before=before,
        offset=offset,
        limit=limit,
    )

    repo = Repository(db_path)
    try:
        results = repo.search(sq)
        return [_to_search_response(r) for r in results]
    finally:
        repo.close()


def _to_search_response(r) -> SearchResultResponse:
    d = r.document
    return SearchResultResponse(
        document=DocumentResponse(
            path=d.path,
            filename=d.filename,
            directory=d.directory,
            extension=d.extension,
            size=d.size,
            mtime=d.mtime,
            metadata=d.combined_metadata,
            indexed_at=d.indexed_at,
        ),
        score=r.score,
        snippet=r.snippet,
    )
