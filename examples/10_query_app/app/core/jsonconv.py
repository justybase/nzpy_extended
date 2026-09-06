"""JSON-safe conversion for Netezza cell values."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
import math
from typing import Any
from uuid import UUID


def jsonable_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        # Keep exact decimal text; converting through float corrupts large
        # NUMERIC/DECIMAL values before they reach the result session/grid.
        return str(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def jsonable_rows(rows: list[list[Any]]) -> list[list[Any]]:
    return [[jsonable_value(cell) for cell in row] for row in rows]
