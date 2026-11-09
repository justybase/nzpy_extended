"""
FastAPI server for RAM-leak investigation.

Endpoints:
  GET  /memory        – current RSS + gc object stats
  POST /query         – execute SQL, return rows + RSS delta
  POST /query-cancel  – partial fetch then cancel, return RSS delta

Env configuration:
  NZ_DEV_HOST, NZ_DEV_PORT, NZ_DEV_DB, NZ_DEV_USER, NZ_DEV_PASSWORD
  MODE_POOL=1      (default) use NzPool; 0 = fresh connection per request
  MODE_GC=1        (default) call gc.collect() after each query
"""

from __future__ import annotations

import gc
import json
import os
import sys
from collections import Counter
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import nzpy_extended as nzpy
from nzpy_extended.pool import NzPool

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
except ImportError as exc:
    print("Missing dependencies. Run: pip install fastapi uvicorn")
    raise SystemExit(1) from exc


_NZ_HOST = os.environ.get("NZ_DEV_HOST", "127.0.0.1")
_NZ_PORT = int(os.environ.get("NZ_DEV_PORT", "5480"))
_NZ_DB = os.environ.get("NZ_DEV_DB", "JUST_DATA")
_NZ_USER = os.environ.get("NZ_DEV_USER", "admin")
_NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD", "password")
_NZ_MIN_POOL = int(os.environ.get("NZ_MIN_POOL", "2"))
_NZ_MAX_POOL = int(os.environ.get("NZ_MAX_POOL", "4"))
_MODE_POOL = int(os.environ.get("MODE_POOL", "1"))
_MODE_GC = int(os.environ.get("MODE_GC", "1"))

_process = psutil.Process(os.getpid())


def _rss_mb() -> float:
    return _process.memory_info().rss / 1_000_000


class _JsonEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, (datetime, date, time)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, bytes):
            return obj.hex()
        return super().default(obj)


def _jsonable_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def _build_columns(cur: Any) -> list[dict[str, Any]]:
    schema = cur.get_schema_table()
    if not schema:
        desc = cur.description
        if desc:
            return [
                {"ColumnName": d[0], "DataType": "unknown", "ColumnOrdinal": i + 1}
                for i, d in enumerate(desc)
            ]
        return []
    for col in schema:
        dt = col.get("DataType")
        if isinstance(dt, type):
            col["DataType"] = dt.__name__
    return schema


async def _get_connection(pool: NzPool | None) -> Any:
    if pool is not None:
        return await pool.acquire()
    conn = nzpy.Connection()
    await conn.connect(
        user=_NZ_USER,
        password=_NZ_PASSWORD,
        host=_NZ_HOST,
        port=_NZ_PORT,
        database=_NZ_DB,
    )
    return conn


async def _release_connection(pool: NzPool | None, conn: Any) -> None:
    if pool is not None:
        await pool.release(conn)
    else:
        await conn.close()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="nzpy_extended RAM Diagnostic", version="2.0")

_pool: NzPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _pool
    if _MODE_POOL:
        _pool = NzPool(
            min_size=_NZ_MIN_POOL,
            max_size=_NZ_MAX_POOL,
            acquire_timeout=10.0,
            ping_query="SELECT 1",
            user=_NZ_USER,
            password=_NZ_PASSWORD,
            host=_NZ_HOST,
            port=_NZ_PORT,
            database=_NZ_DB,
        )
        await _pool.open()
    else:
        _pool = None

    print(f"[CFG] Pool={bool(_MODE_POOL)} GC={bool(_MODE_GC)}  RSS={_rss_mb():.1f} MB", flush=True)
    try:
        yield
    finally:
        if _pool is not None:
            await _pool.close_all()


app.router.lifespan_context = lifespan


@app.get("/memory")
async def memory() -> dict[str, Any]:
    gc.collect()
    types: Counter = Counter()
    for obj in gc.get_objects():
        types[type(obj).__name__] += 1
    return {
        "rss_mb": round(_rss_mb(), 1),
        "gc_objects": sum(types.values()),
        "top_types": dict(types.most_common(15)),
    }


@app.post("/query")
async def run_query(body: dict[str, Any]) -> Any:
    sql = body.get("sql", "").strip()
    if not sql:
        raise HTTPException(400, "SQL query is empty")

    rss_before = _rss_mb()
    conn = await _get_connection(_pool)
    cur = None
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        columns = _build_columns(cur)
        jsonable: list[list[Any]] = []
        async for row in cur:
            jsonable.append([_jsonable_value(v) for v in row])
        row_count = len(jsonable)
    finally:
        if cur is not None:
            await cur.close()
        await _release_connection(_pool, conn)

    rss_after = _rss_mb()
    if _MODE_GC:
        gc.collect()
        rss_after = _rss_mb()

    return JSONResponse(content={
        "columns": columns,
        "row_count": row_count,
        "rows": jsonable,
        "rss_mb_before": round(rss_before, 1),
        "rss_mb_after": round(rss_after, 1),
        "rss_mb_delta": round(rss_after - rss_before, 1),
    })


@app.post("/query-cancel")
async def run_query_cancel(body: dict[str, Any]) -> Any:
    sql = body.get("sql", "").strip()
    if not sql:
        raise HTTPException(400, "SQL query is empty")

    fetch_rows = int(body.get("fetch_rows", 5))
    if fetch_rows < 1:
        raise HTTPException(400, "fetch_rows must be >= 1")

    rss_before = _rss_mb()
    conn = await _get_connection(_pool)
    cur = None
    fetch_rows_read = 0
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        for _ in range(fetch_rows):
            row = await cur.fetchone()
            if row is None:
                break
            fetch_rows_read += 1
        await conn.cancel()
    finally:
        if cur is not None:
            await cur.close()
        await _release_connection(_pool, conn)

    rss_after = _rss_mb()
    if _MODE_GC:
        gc.collect()
        rss_after = _rss_mb()

    return JSONResponse(content={
        "fetch_rows_requested": fetch_rows,
        "fetch_rows_read": fetch_rows_read,
        "rss_mb_before": round(rss_before, 1),
        "rss_mb_after": round(rss_after, 1),
        "rss_mb_delta": round(rss_after - rss_before, 1),
    })


if __name__ == "__main__":
    import uvicorn

    print(f"Starting RSS={_rss_mb():.1f} MB")
    uvicorn.run("server:app", host="0.0.0.0", port=8480, reload=False)
