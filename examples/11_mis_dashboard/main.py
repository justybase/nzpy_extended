#!/usr/bin/env python3
"""
FastAPI MIS dashboard for a retail sales network (example project).

Layered architecture:
    main.py                     app factory + lifespan wiring
    app/api/routes/*            thin HTTP layer (routers + DI)
    app/services/*              business logic (reports, exports, caching)
    app/repositories/*          data access (cached Netezza tables, fake for tests)
    app/schemas/*               Pydantic response models

Existing reports use cached ordinary MIS_* tables. The larger point-in-time
mart is read through parameterized snapshot slices and those slices are cached
too — Netezza data tables are queried at startup, after a published ETL version
change, or on explicit refresh. A small control-table query runs on the
background polling interval.

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
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles

from app.api.routes import api_router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.pool import build_pool
from app.repositories import CachedMISRepository
from app.services.auth_service import AuthService
from app.services.cache_coordinator import CacheCoordinator
from app.services.export_service import ExportService
from app.services.fake_ldap import FakeLDAPService
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
            sqlite_path=settings.cache_sqlite_path,
        )
        app.state.repository = repository
        app.state.report_service = ReportService(
            repository, ttl=settings.cache_ttl_reports)
        app.state.export_service = ExportService(app.state.report_service)
        app.state.ledger_service = LedgerService(repository)
        app.state.people_service = PeopleService(
            repository, ttl=settings.cache_ttl_reports)
        app.state.session_service = SessionService(repository)
        app.state.auth_service = AuthService(
            app.state.session_service,
            FakeLDAPService(),
            settings.jwt_secret,
            settings.jwt_ttl_minutes,
            settings.auth_issuer,
        )
        app.state.temporal_service = TemporalMISService(
            repository, ttl=settings.cache_ttl_reports)
        app.state.cache_coordinator = CacheCoordinator(
            repository,
            pool,
            app.state.report_service,
            app.state.people_service,
            app.state.ledger_service,
            app.state.temporal_service,
            dataset_name=settings.cache_dataset_name,
            control_table_name=settings.cache_control_table,
            poll_seconds=settings.cache_control_poll_seconds,
            max_unconfirmed_seconds=settings.cache_max_unconfirmed_seconds,
        )

        refresh_task: asyncio.Task[None] | None = None
        try:
            # Preload eager dimensions/facts. The large performance mart is
            # intentionally fetched and cached as date slices on demand.
            result = await app.state.cache_coordinator.initialize()
            logger.info("initial table load: %s", result)
            refresh_task = asyncio.create_task(
                app.state.cache_coordinator.refresh_loop(
                    settings.cache_control_poll_seconds))
            yield
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
            await pool.close_all()

    app = FastAPI(
        title="Retail Sales Network MIS Dashboard",
        version="1.0",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "authentication", "description": "Login and logout."},
            {"name": "meta", "description": "Report catalog and cache metadata."},
            {"name": "reports", "description": "Monthly report payloads."},
            {"name": "point-in-time MIS", "description": "As-of MIS and quality APIs."},
            {"name": "drill", "description": "Authorized report drill-downs."},
            {"name": "exports", "description": "Authorized spreadsheet exports."},
            {"name": "ledger", "description": "Paged and filtered sales detail."},
            {"name": "people", "description": "Advisor and branch views."},
            {"name": "session", "description": "Effective persona session APIs."},
            {"name": "cache", "description": "Authorized cache lifecycle operations."},
            {"name": "status", "description": "Authenticated service status."},
        ],
    )
    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

    def custom_openapi() -> dict[str, Any]:
        """Expose the cookie security contract in generated OpenAPI."""
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=(
                "Reporting API for the retail sales network. Data endpoints "
                "require the configured HttpOnly access cookie."
            ),
            routes=app.routes,
            tags=app.openapi_tags,
        )
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["accessCookie"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": settings.auth_cookie_name,
            "description": "HttpOnly session cookie issued by the login endpoint.",
        }
        public_operations = {
            ("/api/auth/login", "post"),
            ("/api/auth/logout", "post"),
        }
        for path, path_item in schema.get("paths", {}).items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if (path, method) not in public_operations:
                    operation["security"] = [{"accessCookie": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi
    return app


app: FastAPI = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=_settings.host, port=_settings.port, reload=True)
