"""FastAPI dependency injection.

Services are wired in the app lifespan (see server.create_app) and exposed
to routes through request.app.state — routers stay thin and tests can swap
implementations freely.
"""

from __future__ import annotations

from fastapi import Request

from app.core.config import Settings
from app.services.query_service import QueryService
from app.services.schema_service import SchemaService
from app.services.result_session_service import ResultSessionManager
from app.services.sql_safety import SqlSafetyService
from app.services.language_service import LanguageService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_query_service(request: Request) -> QueryService:
    return request.app.state.query_service


def get_schema_service(request: Request) -> SchemaService:
    return request.app.state.schema_service


async def get_pool(request: Request):  # type: ignore[no-untyped-def]
    return await request.app.state.pool_manager.get_pool()


def get_pool_manager(request: Request):  # type: ignore[no-untyped-def]
    return request.app.state.pool_manager


def get_result_sessions(request: Request) -> ResultSessionManager:
    return request.app.state.result_sessions


def get_sql_safety(request: Request) -> SqlSafetyService:
    return request.app.state.sql_safety


def get_language_service(request: Request) -> LanguageService:
    return request.app.state.language_service
