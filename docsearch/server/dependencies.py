from __future__ import annotations

from docsearch.config import Config, default_config

# Set at startup by app.py; read by route modules via Depends(get_config).
_app_config: Config | None = None


def get_config() -> Config:
    """FastAPI dependency that returns the app's Config."""
    if _app_config is None:
        return default_config()
    return _app_config
