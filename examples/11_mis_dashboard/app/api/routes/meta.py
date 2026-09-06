"""GET /api/meta — available months, cache status, report list."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import (get_report_service, get_repository,
                          get_temporal_service)
from app.repositories.base import MISRepository
from app.schemas.report import MetaResponse
from app.services.report_service import ReportService
from app.services.temporal_service import TemporalMISService

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta", response_model=MetaResponse)
async def meta(repository: MISRepository = Depends(get_repository),
               report_service: ReportService = Depends(get_report_service),
               temporal_service: TemporalMISService = Depends(get_temporal_service)) -> dict[str, Any]:
    months = await report_service.available_months()
    snapshots = await temporal_service.available_snapshots()
    cache_info: dict[str, Any] = repository.snapshot()
    cache_info.update(report_service.cache_info())
    return {
        "months": months,
        "snapshot_dates": snapshots,
        "latest_snapshot": snapshots[-1] if snapshots else None,
        "cache": cache_info,
        "reports": report_service.report_list(),
    }
