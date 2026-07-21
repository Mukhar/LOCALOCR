"""LOCALOCR web-UI package (Phase 3).

Exposes the :func:`create_app` factory used by ``python -m src.web``
and by the test suite. Nothing outside this package should reach into
``src.web.services.*`` -- the FastAPI app is the public surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI

from .services.db import init_db


def create_app(db_path: Optional[Path] = None) -> FastAPI:
    """Build a fresh FastAPI app instance.

    Kept as a factory (not a module-level global) so tests can spin
    up isolated apps with their own tmp SQLite files.
    """
    app = FastAPI(title="LOCALOCR", version="1.1")
    app.state.db_path = db_path or Path("output/localocr.sqlite").resolve()
    init_db(app.state.db_path)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "db_path": str(app.state.db_path)}

    return app
