"""POST /api/cache/refresh — reload all tables from Netezza."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_report_service, get_repository
from app.repositories.base import MISRepository
from app.services.report_service import ReportService

router = APIRouter(prefix="/api/cache", tags=["cache"])


@router.post("/refresh")
async def refresh(repository: MISRepository = Depends(get_repository),
                  report_service: ReportService = Depends(get_report_service)) -> dict[str, Any]:
    result = await repository.refresh_all()
    report_service.clear_cache()  # computed payloads must be rebuilt from fresh data
    result["report_cache_cleared"] = True
    return result