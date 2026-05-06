from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .routes.documents import router as documents_router
from .routes.index import router as index_router
from .routes.search import router as search_router

# Default DB location
_DEFAULT_DB = os.path.expanduser("~/.local/share/docsearch/docsearch.db")


def _get_db_path() -> str:
    return os.environ.get("DOCSEARCH_DB", _DEFAULT_DB)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    db_path = _get_db_path()
    # Ensure DB directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    app.state.db_path = db_path
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="docsearch",
        description="Document metadata index and search engine",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(index_router)
    app.include_router(search_router)
    app.include_router(documents_router)

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "db": app.state.db_path}

    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run("docsearch.server.app:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()