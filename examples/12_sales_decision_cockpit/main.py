#!/usr/bin/env python3
"""FastAPI entry point for the Sales Decision Cockpit.

Usage:
    python seed.py
    python main.py       # http://127.0.0.1:8482
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import api_router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.pool import build_pool
from app.repositories import CachedSDCRepository
from app.services.cockpit_service import CockpitService
from app.services.export_service import ExportService

logger = logging.getLogger("sdc.main")
_settings = Settings()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or _settings
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        pool = await build_pool(settings)
        repository = CachedSDCRepository(pool, list(settings.table_names), settings.cache_ttl_tables)
        app.state.pool = pool
        app.state.settings = settings
        app.state.repository = repository
        app.state.cockpit_service = CockpitService(repository, settings.cache_ttl_reports)
        app.state.export_service = ExportService(app.state.cockpit_service)
        try:
            result = await repository.refresh_all()
            logger.info("initial SDC cache load: %s", result)
            yield
        finally:
            await pool.close_all()

    app = FastAPI(
        title="Sales Decision Cockpit",
        version="1.0",
        description="Exception-first weekly sales reporting on Netezza.",
        lifespan=lifespan,
    )
    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    runtime_settings = Settings.from_env()
    uvicorn.run(create_app(runtime_settings), host=runtime_settings.host, port=runtime_settings.port)
