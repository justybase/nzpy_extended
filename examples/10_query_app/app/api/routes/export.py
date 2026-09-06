"""Export route — re-runs the SQL and streams CSV/JSON."""

from __future__ import annotations

import io
import json

import nzpy_extended as nzpy
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_pool, get_settings
from app.api.form import read_form
from app.core.config import Settings
from app.services.data_exchange import rows_to_csv
from app.services.query_service import build_columns
from app.services.sql_document import is_write_statement, split_statements

router = APIRouter(tags=["export"])


@router.post("/api/export")
async def export_results(
    request: Request,
    settings: Settings = Depends(get_settings),
    pool=Depends(get_pool),  # type: ignore[no-untyped-def]
) -> StreamingResponse:
    form = await read_form(request)
    sql = str(form.get("sql", ""))
    format = str(form.get("format", "csv"))
    if not sql.strip():
        raise HTTPException(400, "SQL query is empty")
    if format not in {"csv", "json"}:
        raise HTTPException(400, "format must be csv or json")
    if any(is_write_statement(statement.sql) for statement in split_statements(sql)):
        raise HTTPException(400, "Export accepts read-only SQL statements.")
    try:
        async with pool.connection() as conn:
            cur = conn.cursor()
            try:
                await cur.execute(sql, timeout=settings.default_query_timeout)
                rows = await cur.fetchall()
                columns = build_columns(cur)
            finally:
                await cur.close()
    except nzpy.Error as exc:
        raise HTTPException(500, str(exc)) from exc

    py_rows = [list(r) for r in rows]
    if format == "json":
        header = [c.get("ColumnName", "?") for c in columns]
        payload = [{header[i]: v for i, v in enumerate(r)} for r in py_rows]
        output = io.StringIO(json.dumps(payload, indent=2, default=str))
        return StreamingResponse(
            output,
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="export.json"'},
        )
    output = rows_to_csv(columns, py_rows)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="export.csv"'},
    )
