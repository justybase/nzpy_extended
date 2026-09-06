#!/usr/bin/env python3
"""
FastAPI MIS dashboard for a retail sales network (example project).

Layered architecture:
    main.py                     app factory + lifespan wiring
    app/api/routes/*            thin HTTP layer (routers + DI)
    app/services/*              business logic (reports, exports, caching)
    app/repositories/*          data access (cached Netezza tables, fake for tests)
    app/schemas/*               Pydantic response models

Existing reports use cached MIS_* tables. The larger point-in-time mart is
read through parameterized snapshot slices and those slices are cached too —
Netezza is queried only on a cold/expired cache or explicit refresh.

Usage:
    python seed.py   # create & populate the MIS_* tables first
    python main.py   # start on http://0.0.0.0:8481
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root: nzpy_extended

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import api_router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.pool import build_pool
from app.repositories import CachedMISRepository
from app.services.export_service import ExportService
from app.services.ledger_service import LedgerService
from app.services.people_service import PeopleService
from app.services.report_service import ReportService
from app.services.session_service import SessionService
from app.services.temporal_service import TemporalMISService

logger = logging.getLogger("mis.main")


_settings = Settings.from_env()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or _settings
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        pool = await build_pool(settings)
        app.state.pool = pool
        app.state.settings = settings

        repository = CachedMISRepository(
            pool,
            list(settings.table_names),
            ttl_seconds=settings.cache_ttl_tables,
            lazy_table_names={"MIS_FACT_PERFORMANCE_SNAPSHOT"},
        )
        app.state.repository = repository
        app.state.report_service = ReportService(
            repository, ttl=settings.cache_ttl_reports)
        app.state.export_service = ExportService(app.state.report_service)
        app.state.ledger_service = LedgerService(repository)
        app.state.people_service = PeopleService(
            repository, ttl=settings.cache_ttl_reports)
        app.state.session_service = SessionService(repository)
        app.state.temporal_service = TemporalMISService(
            repository, ttl=settings.cache_ttl_reports)

        refresh_task: asyncio.Task[None] | None = None
        try:
            # Preload eager dimensions/facts. The large performance mart is
            # intentionally fetched and cached as date slices on demand.
            result = await repository.refresh_all()
            logger.info("initial table load: %s", result)
            refresh_task = asyncio.create_task(
                repository.refresh_loop(settings.cache_refresh_seconds))
            yield
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
            await pool.close_all()

    app = FastAPI(
        title="Retail Sales Network MIS Dashboard",
        version="1.0",
        lifespan=lifespan,
    )
    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
    return app


app: FastAPI = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=_settings.host, port=_settings.port, reload=True)
