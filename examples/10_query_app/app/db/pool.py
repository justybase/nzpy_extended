"""Netezza connection pool factory.

The pool is the only component that talks to the database directly;
services acquire connections from it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nzpy_extended.pool import NzPool

from app.core.config import Settings


async def build_pool(settings: Settings) -> NzPool:
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


class DatabasePoolManager:
    """Lazily owns one bounded pool per database selected in the workspace."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._pools: dict[str, NzPool] = {}
        self._lock = asyncio.Lock()

    async def get_pool(self, database: str | None = None) -> NzPool:
        name = (database or self.settings.nz_database).strip() or self.settings.nz_database
        async with self._lock:
            pool = self._pools.get(name.upper())
            if pool is None:
                scoped = Settings(**{**self.settings.__dict__, "nz_database": name})
                pool = await build_pool(scoped)
                self._pools[name.upper()] = pool
            return pool

    async def close_all(self) -> None:
        for pool in list(self._pools.values()):
            await pool.close_all()
        self._pools.clear()

    async def stats(self) -> dict[str, Any]:
        return {database: pool_stats(pool) for database, pool in self._pools.items()}


def pool_stats(pool: NzPool) -> dict[str, Any]:
    get_stats = getattr(pool, "get_stats", None)
    if callable(get_stats):
        return dict(get_stats())
    return {
        "min_size": getattr(pool, "min_size", None),
        "max_size": getattr(pool, "max_size", None),
    }
