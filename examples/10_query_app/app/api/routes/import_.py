"""Import route — CSV upload via load_data in batches."""

from __future__ import annotations

import re

import nzpy_extended as nzpy
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import get_pool, get_settings
from app.core.config import Settings
from app.schemas.models import ImportResponse
from app.services.data_exchange import (
    coerce_delimiter,
    csv_to_rows,
    header_columns,
)

router = APIRouter(tags=["import"])

_BATCH = 5000
_TABLE_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")


@router.post("/api/import", response_model=ImportResponse)
async def import_data(
    file: UploadFile = File(...),
    table: str = Form(...),
    delimiter: str = Form(","),
    settings: Settings = Depends(get_settings),
    pool=Depends(get_pool),  # type: ignore[no-untyped-def]
) -> ImportResponse:
    if not file.filename:
        raise HTTPException(400, "No file provided")
    if not _TABLE_PATH_RE.fullmatch(table.strip()):
        raise HTTPException(400, "table must be an unquoted database.schema.table identifier")
    content = (await file.read()).decode("utf-8-sig")
    if not content.strip():
        raise HTTPException(400, "File is empty")

    delim = coerce_delimiter(delimiter)
    rows = csv_to_rows(content, delim)
    if not rows:
        raise HTTPException(400, "No data rows found")

    header = [(c or "").replace('"', "").strip() or f"col{i+1}"
              for i, c in enumerate(rows[0])]
    data_rows = rows[1:]
    total_rows = len(data_rows)
    columns_schema = header_columns(header)

    imported = 0
    errors: list[str] = []
    async with pool.connection() as conn:
        for start in range(0, min(total_rows, settings.max_import_rows), _BATCH):
            batch = data_rows[start:start + _BATCH]
            try:
                await conn.load_data(
                    table_name=table, rows=batch, columns=columns_schema
                )
                imported += len(batch)
            except nzpy.Error as exc:
                errors.append(f"Batch {start}-{start + len(batch)}: {exc}")
                break

    return ImportResponse(
        table=table,
        total_rows_in_file=total_rows,
        imported=imported,
        errors=errors or None,
    )
