"""Spreadsheet exports for the current report context."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.api.deps import get_export_service
from app.services.cockpit_service import DataNotReady
from app.services.export_service import ExportResult, ExportService

router = APIRouter(prefix="/api/export", tags=["exports"])


def _remove_export(path: str) -> None:
    Path(path).unlink(missing_ok=True)


async def _send(result: ExportResult) -> FileResponse:
    return FileResponse(
        result.path,
        media_type=result.media_type,
        filename=result.filename,
        background=BackgroundTask(_remove_export, result.path),
    )


@router.get("/cockpit/{fmt}")
async def export_cockpit(
    fmt: str,
    week: str | None = Query(default=None),
    lookback: int = Query(default=12),
    scope: str = Query(default="network"),
    region: str | None = Query(default=None),
    unit: str | None = Query(default=None),
    metric: str = Query(default="sales_amount"),
    service: ExportService = Depends(get_export_service),
) -> FileResponse:
    try:
        result = await service.export_cockpit(fmt, week=week, lookback=lookback, scope=scope, region=region, unit=unit, metric=metric)
        return await _send(result)
    except DataNotReady as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/drivers/{fmt}")
async def export_drivers(
    fmt: str,
    week: str | None = Query(default=None),
    lookback: int = Query(default=12),
    scope: str = Query(default="network"),
    region: str | None = Query(default=None),
    unit: str | None = Query(default=None),
    metric: str = Query(default="sales_amount"),
    dimension: str = Query(default="product"),
    service: ExportService = Depends(get_export_service),
) -> FileResponse:
    try:
        result = await service.export_drivers(fmt, week=week, lookback=lookback, scope=scope, region=region, unit=unit, metric=metric, dimension=dimension)
        return await _send(result)
    except DataNotReady as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
