"""GET /api/report/{report_id} — full report payload (KPIs + tables + charts)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

import nzpy_extended as nzpy

from app.api.deps import get_report_service
from app.schemas.report import ReportResponse
from app.services import reporting
from app.services.report_service import ReportService

router = APIRouter(prefix="/api/report", tags=["reports"])


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: str,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    dim: str | None = Query(default=None),
    report_service: ReportService = Depends(get_report_service),
) -> dict[str, Any]:
    try:
        return await report_service.build(report_id, from_, to, dim)
    except reporting.UnknownReport:
        raise HTTPException(404, f"Unknown report: {report_id}") from None
    except reporting.DataNotReady as exc:
        raise HTTPException(503, str(exc)) from None
    except nzpy.Error as exc:
        hint = " — run: python seed.py" if "not found" in str(exc).lower() else ""
        raise HTTPException(503, f"Netezza error: {exc}{hint}") from None