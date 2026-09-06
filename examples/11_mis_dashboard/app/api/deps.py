"""FastAPI dependency injection.

Services are wired in the app lifespan (see main.create_app) and exposed to
routes through request.app.state — this keeps the routers thin and lets tests
swap implementations freely.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.core.config import Settings
from app.core.roles import SessionUser
from app.repositories.base import MISRepository
from app.services.export_service import ExportService
from app.services.auth_service import AuthService, InvalidToken
from app.services.cache_coordinator import CacheCoordinator
from app.services.ledger_service import LedgerService
from app.services.people_service import PeopleService
from app.services.report_service import ReportService
from app.services.session_service import SessionService
from app.services.temporal_service import TemporalMISService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_repository(request: Request) -> MISRepository:
    return request.app.state.repository


def get_report_service(request: Request) -> ReportService:
    return request.app.state.report_service


def get_export_service(request: Request) -> ExportService:
    return request.app.state.export_service


def get_ledger_service(request: Request) -> LedgerService:
    return request.app.state.ledger_service


def get_people_service(request: Request) -> PeopleService:
    return request.app.state.people_service


def get_session_service(request: Request) -> SessionService:
    return request.app.state.session_service


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_temporal_service(request: Request) -> TemporalMISService:
    return request.app.state.temporal_service


def get_cache_coordinator(request: Request) -> CacheCoordinator:
    return request.app.state.cache_coordinator


async def get_current_user(request: Request,
                          auth_service: AuthService = Depends(get_auth_service)) -> SessionUser:
    token = request.cookies.get(request.app.state.settings.auth_cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return await auth_service.authenticate(token)
    except InvalidToken:
        raise HTTPException(status_code=401, detail="Invalid or expired access token") from None
