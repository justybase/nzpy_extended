"""People API — advisor performance panels and personal cumulative views."""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.api.deps import (get_current_user, get_export_service,
                          get_people_service)
from app.schemas.people import (AdvisorListResponse, AdvisorPanelResponse,
                                BranchListResponse, CumulativeResponse)
from app.services.export_service import ExportService, MEDIA_TYPES
from app.services.people_service import Forbidden, PeopleService, UnknownEntity
from app.services.session_service import SessionUser

router = APIRouter(prefix="/api/people", tags=["people"])


@router.get("/advisors", response_model=AdvisorListResponse)
async def advisor_list(
    people_service: PeopleService = Depends(get_people_service),
    user: SessionUser = Depends(get_current_user),
) -> dict[str, Any]:
    return {"advisors": await people_service.advisor_list(user)}


@router.get("/branches", response_model=BranchListResponse)
async def branch_list(
    people_service: PeopleService = Depends(get_people_service),
    user: SessionUser = Depends(get_current_user),
) -> dict[str, Any]:
    return {"branches": await people_service.branch_list(user)}


@router.get("/advisors/{code}", response_model=AdvisorPanelResponse)
async def advisor_panel(
    code: str,
    people_service: PeopleService = Depends(get_people_service),
    user: SessionUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return await people_service.advisor_panel(code, user)
    except UnknownEntity as exc:
        raise HTTPException(404, str(exc)) from None
    except Forbidden as exc:
        raise HTTPException(403, str(exc)) from None


@router.get("/cumulative", response_model=CumulativeResponse)
async def cumulative(
    scope: str = Query(default="branch"),
    code: str = Query(...),
    month: str = Query(...),
    people_service: PeopleService = Depends(get_people_service),
    user: SessionUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return await people_service.cumulative(scope, code, month, user)
    except UnknownEntity as exc:
        raise HTTPException(404, str(exc)) from None
    except Forbidden as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/advisors/{code}/export/{fmt}")
async def advisor_panel_export(
    code: str,
    fmt: str,
    people_service: PeopleService = Depends(get_people_service),
    export_service: ExportService = Depends(get_export_service),
    user: SessionUser = Depends(get_current_user),
) -> FileResponse:
    if fmt not in MEDIA_TYPES:
        raise HTTPException(400, f"fmt must be one of {sorted(MEDIA_TYPES)}")
    try:
        payload = await people_service.advisor_panel(code, user)
    except UnknownEntity as exc:
        raise HTTPException(404, str(exc)) from None
    except Forbidden as exc:
        raise HTTPException(403, str(exc)) from None
    meta_lines: list[list[Any]] = [
        ["Report", "advisor_performance"],
        ["Advisor", code],
        ["Name", payload["info"]["name"]],
        ["Rows", len(payload["rows"])],
        ["Generated", dt.datetime.now().isoformat(timespec="seconds")],
        ["Data source", "cached MIS_* tables (TTLCache)"],
    ]
    # drop the hidden 'ym' sort key column, as report exports do
    columns = payload["columns"][1:]
    rows = [r[1:] for r in payload["rows"]]
    path = export_service.write_workbook(
        fmt, f"Advisor ratings - {code}"[:31], columns, rows, meta_lines)
    return FileResponse(
        path,
        media_type=MEDIA_TYPES[fmt],
        filename=f"advisor_{code}_ratings.{fmt}",
        background=BackgroundTask(os.remove, path),
    )


@router.get("/cumulative/export/{fmt}")
async def cumulative_export(
    scope: str,
    code: str,
    month: str,
    fmt: str,
    people_service: PeopleService = Depends(get_people_service),
    export_service: ExportService = Depends(get_export_service),
    user: SessionUser = Depends(get_current_user),
) -> FileResponse:
    if fmt not in MEDIA_TYPES:
        raise HTTPException(400, f"fmt must be one of {sorted(MEDIA_TYPES)}")
    try:
        payload = await people_service.cumulative(scope, code, month, user)
    except UnknownEntity as exc:
        raise HTTPException(404, str(exc)) from None
    except Forbidden as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    meta_lines: list[list[Any]] = [
        ["Report", f"cumulative_{scope}"],
        ["Entity", code],
        ["Name", payload["info"]["name"]],
        ["Month", month],
        ["Rows", len(payload["rows"])],
        ["Generated", dt.datetime.now().isoformat(timespec="seconds")],
        ["Data source", "cached MIS_* tables (TTLCache)"],
    ]
    path = export_service.write_workbook(
        fmt, f"Cumulative {scope} - {code}"[:31], payload["columns"],
        payload["rows"], meta_lines)
    return FileResponse(
        path,
        media_type=MEDIA_TYPES[fmt],
        filename=f"cumulative_{scope}_{code}_{month}.{fmt}",
        background=BackgroundTask(os.remove, path),
    )