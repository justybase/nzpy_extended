"""GET /api/drill/{report_id}/{target} — row-level drill-down.

target in {branches, advisors, sales}:
    branches -> branches of a region (key = region code)
    advisors -> advisors of a branch (key = branch code)
    sales    -> individual sales of an advisor (key = advisor code)

Optional fmt=xlsx|xlsb returns the drill table as a spreadsheet download.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.api.deps import (get_current_user, get_export_service,
                          get_report_service)
from app.core.roles import SessionUser
from app.schemas.report import DrillResponse
from app.services import reporting
from app.services.export_service import ExportService
from app.services.report_service import ReportService

router = APIRouter(prefix="/api/drill", tags=["drill"])


@router.get("/{report_id}/{target}", response_model=DrillResponse)
async def drill(
    report_id: str,
    target: str,
    key: str = Query(..., description="code of the parent entity (region/branch/advisor)"),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    fmt: str | None = Query(default=None, description="optional xlsx|xlsb download"),
    report_service: ReportService = Depends(get_report_service),
    export_service: ExportService = Depends(get_export_service),
    user: SessionUser = Depends(get_current_user),
) -> Any:
    if target not in reporting.DRILL_TARGETS:
        raise HTTPException(400, f"target must be one of {sorted(reporting.DRILL_TARGETS)}")
    try:
        if fmt is not None:
            result = await export_service.export_drill(report_id, target, key, fmt,
                                                       from_, to, user)
            return FileResponse(
                result.path,
                media_type=result.media_type,
                filename=result.filename,
                background=BackgroundTask(os.remove, result.path),
            )
        return await report_service.drill(report_id, target, key, from_, to, user)
    except reporting.UnknownReport:
        raise HTTPException(404, f"Unknown report: {report_id}") from None
    except reporting.DrillNotAvailable as exc:
        raise HTTPException(400, str(exc)) from None
    except reporting.DrillKeyNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except reporting.DataNotReady as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None