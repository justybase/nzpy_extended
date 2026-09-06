"""Netezza pool factory; application services never create connections."""

from __future__ import annotations

from typing import Any

from nzpy_extended.pool import NzPool

from app.core.config import Settings


async def build_pool(settings: Settings) -> NzPool:
    settings.require_connection()
    pool = NzPool(
        min_size=settings.nz_min_pool,
        max_size=settings.nz_max_pool,
        acquire_timeout=10.0,
        ping_query="SELECT 1",
        user=settings.nz_user,
        password=settings.nz_password,
        host=settings.nz_host,
        port=settings.nz_port,
        database=settings.nz_database,
    )
    await pool.open()
    return pool


def pool_stats(pool: NzPool) -> dict[str, Any]:
    return dict(pool.get_stats())
