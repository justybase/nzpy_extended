"""GET /api/ledger — paginated, filterable sales detail (large analytics view)."""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.api.deps import get_export_service, get_ledger_service
from app.schemas.ledger import LedgerResponse
from app.services.export_service import ExportService, MEDIA_TYPES
from app.services.ledger_service import EXPORT_CAP, LedgerService

router = APIRouter(prefix="/api/ledger", tags=["ledger"])

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@router.get("", response_model=LedgerResponse)
async def ledger(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    q: str | None = Query(default=None),
    group: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    status: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    dir: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ledger_service: LedgerService = Depends(get_ledger_service),
) -> dict[str, Any]:
    if dir and dir.lower() not in ("asc", "desc"):
        raise HTTPException(400, "dir must be 'asc' or 'desc'")

    # validate filter values against the dimension tables (cached)
    filters = await ledger_service.filters()
    for value, allowed, name in ((group, filters["groups"], "group"),
                                 (channel, filters["channels"], "channel"),
                                 (status, filters["statuses"], "status")):
        if value is not None and value not in allowed:
            raise HTTPException(400, f"Unknown {name}: {value}")

    return await ledger_service.query(from_, to, q, group, channel, status,
                                      sort, dir, page, page_size)


@router.get("/export/{fmt}")
async def ledger_export(
    fmt: str,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    q: str | None = Query(default=None),
    group: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    status: str | None = Query(default=None),
    ledger_service: LedgerService = Depends(get_ledger_service),
    export_service: ExportService = Depends(get_export_service),
) -> FileResponse:
    if fmt not in MEDIA_TYPES:
        raise HTTPException(400, f"fmt must be one of {sorted(MEDIA_TYPES)}")
    result = await ledger_service.export_rows(from_, to, q, group, channel,
                                              status)
    if not result["rows"]:
        raise HTTPException(404, "No rows match the current filters")
    period = f"{from_ or 'start'}_{to or 'end'}"
    sheet_name = f"Sales ledger {period}"[:31]
    meta_lines: list[list[Any]] = [
        ["Report", "sales_ledger"],
        ["Rows", len(result["rows"])],
        ["Filter q", q or "-"],
        ["Filter group", group or "-"],
        ["Filter channel", channel or "-"],
        ["Filter status", status or "-"],
        ["Truncated at cap", f"{EXPORT_CAP:,}" if result["truncated"] else "no"],
        ["Generated", dt.datetime.now().isoformat(timespec="seconds")],
        ["Data source", "cached MIS_* tables (TTLCache)"],
    ]
    path = export_service.write_workbook(fmt, sheet_name, result["columns"],
                                         result["rows"], meta_lines)
    filename = f"ledger_{period}_{dt.datetime.now():%Y%m%d}.{fmt}"
    return FileResponse(
        path,
        media_type=MEDIA_TYPES[fmt],
        filename=filename,
        background=BackgroundTask(os.remove, path),
    )