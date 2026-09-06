"""FastAPI dependency helpers."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from app.core.config import Settings
from app.repositories.base import SDCRepository
from app.services.cockpit_service import CockpitService
from app.services.export_service import ExportService


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_cockpit_service(request: Request) -> CockpitService:
    return cast(CockpitService, request.app.state.cockpit_service)


def get_export_service(request: Request) -> ExportService:
    return cast(ExportService, request.app.state.export_service)


def get_repository(request: Request) -> SDCRepository:
    return cast(SDCRepository, request.app.state.repository)
