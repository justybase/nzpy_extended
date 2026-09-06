"""GET /api/export/{report_id}/{kind}/{fmt} — XLSX / XLSB downloads (xlspy)."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.api.deps import get_current_user, get_export_service
from app.core.roles import SessionUser
from app.services import reporting
from app.services.export_service import ExportService

router = APIRouter(prefix="/api/export", tags=["exports"])


@router.get("/{report_id}/{kind}/{fmt}")
async def export_report(
    report_id: str,
    kind: str,
    fmt: str,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    dim: str | None = Query(default=None),
    as_of: str | None = Query(default=None),
    attribution: str = Query(default="historical"),
    export_service: ExportService = Depends(get_export_service),
    user: SessionUser = Depends(get_current_user),
) -> FileResponse:
    try:
        result = await export_service.export_report(report_id, kind, fmt, from_, to,
                                                    dim, user, as_of, attribution)
    except reporting.UnknownReport:
        raise HTTPException(404, f"Unknown report: {report_id}") from None
    except reporting.DataNotReady as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return FileResponse(
        result.path,
        media_type=result.media_type,
        filename=result.filename,
        background=BackgroundTask(os.remove, result.path),
    )
