"""FastAPI dependency injection.

Services are wired in the app lifespan (see main.create_app) and exposed to
routes through request.app.state — this keeps the routers thin and lets tests
swap implementations freely.
"""

from __future__ import annotations

from fastapi import Request

from app.core.config import Settings
from app.core.roles import SessionUser
from app.repositories.base import MISRepository
from app.services.export_service import ExportService
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


def get_temporal_service(request: Request) -> TemporalMISService:
    return request.app.state.temporal_service


async def get_current_user(request: Request) -> SessionUser:
    return await request.app.state.session_service.current()
