from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from docsearch.config import Config, default_config
from .dependencies import _app_config as _cfg_ref
from .routes.documents import router as documents_router
from .routes.index import router as index_router
from .routes.search import router as search_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    config = default_config()
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    # Store on app.state for routes to access via Depends
    app.state.config = config
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
        config: Config = app.state.config
        return {"status": "ok", "home": str(config.home), "db": str(config.db_path)}

    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run("docsearch.server.app:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
