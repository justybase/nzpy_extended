#!/usr/bin/env python
"""Modular FastAPI + Netezza SQL workspace."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import api_router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.pool import DatabasePoolManager
from app.services.language_service import LanguageService
from app.services.query_service import QueryService
from app.services.result_session_service import ResultSessionManager
from app.services.schema_service import SchemaService
from app.services.sql_safety import SqlSafetyService

_settings = Settings.from_env()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or _settings
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        pool_manager = DatabasePoolManager(settings)
        await pool_manager.get_pool()
        result_sessions = ResultSessionManager(settings.result_storage_dir, settings.result_session_ttl, settings.result_page_size)
        app.state.pool_manager = pool_manager
        app.state.nz_pool = await pool_manager.get_pool()
        app.state.settings = settings
        app.state.result_sessions = result_sessions
        app.state.sql_safety = SqlSafetyService(settings.preview_ttl)
        app.state.schema_service = SchemaService(lambda database=None: pool_manager.get_pool(database), ttl_seconds=settings.schema_cache_ttl)
        app.state.language_service = LanguageService(app.state.schema_service)
        app.state.query_service = QueryService(
            lambda database=None: pool_manager.get_pool(database),
            result_sessions,
            result_limit=settings.result_limit,
            chunk_size=settings.result_chunk_size,
        )
        try:
            yield
        finally:
            result_sessions.close_all()
            await pool_manager.close_all()

    app = FastAPI(title="nzpy_extended SQL Workspace", version="3.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(Exception)
    async def _global_exc_handler(request: Any, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

    app.include_router(api_router)
    dist_dir = settings.static_dir / "dist"
    static_root = dist_dir if dist_dir.exists() else settings.static_dir
    app.mount("/static", StaticFiles(directory=str(static_root)), name="static")
    return app


app: FastAPI = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host=_settings.host, port=_settings.port, reload=True)
