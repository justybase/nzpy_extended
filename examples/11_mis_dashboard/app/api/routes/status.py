"""GET /api/status — database, pool and cache statistics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_user, get_report_service, get_repository, get_settings
from app.core.config import Settings
from app.repositories.base import MISRepository
from app.schemas.report import StatusResponse
from app.services.report_service import ReportService

router = APIRouter(prefix="/api", tags=["status"],
                   dependencies=[Depends(get_current_user)])


@router.get("/status", response_model=StatusResponse)
async def status(
    request: Request,
    settings: Settings = Depends(get_settings),
    repository: MISRepository = Depends(get_repository),
    report_service: ReportService = Depends(get_report_service),
) -> dict[str, Any]:
    cache_info: dict[str, Any] = repository.snapshot()
    cache_info.update(report_service.cache_info())
    return {
        "database": settings.nz_database,
        "host": settings.nz_host,
        "pool": request.app.state.pool.get_stats(),
        "cache": cache_info,
    }
