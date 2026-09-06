"""Version / status routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_pool
from app.db.pool import pool_stats

router = APIRouter(tags=["meta"])


@router.get("/api/version")
async def get_version(pool=Depends(get_pool)) -> dict[str, str]:  # type: ignore[no-untyped-def]
    async with pool.connection() as conn:
        cur = conn.cursor()
        try:
            await cur.execute("SELECT version()")
            row = await cur.fetchone()
        finally:
            await cur.close()
    return {"version": row[0] if row else "unknown"}


@router.get("/api/status")
async def get_status(pool=Depends(get_pool)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {"pool": pool_stats(pool)}
