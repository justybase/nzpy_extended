"""
CachedMISRepository — complete Netezza MIS_* tables held in memory.

Implements the MISRepository interface on top of a cachetools.TTLCache.
Netezza is queried only when the cache is cold, when an entry expires (TTL),
or on an explicit refresh (`/api/cache/refresh` / background warm-refresh).

Values are normalized at load time (dates -> ISO strings, Decimal -> float)
so the service layer never touches driver-specific types.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from decimal import Decimal
from typing import Any

from cachetools import TTLCache

from app.repositories.base import MISRepository

logger = logging.getLogger("mis.cache")


def _normalize(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


class CachedMISRepository(MISRepository):
    def __init__(
        self,
        pool: Any,
        table_names: list[str],
        ttl_seconds: int = 900,
    ) -> None:
        self._pool = pool
        self._table_names = list(table_names)
        self._ttl = ttl_seconds
        self._cache: TTLCache = TTLCache(maxsize=64, ttl=ttl_seconds)
        self._lock = asyncio.Lock()
        self._meta: dict[str, dict[str, Any]] = {}
        self._last_error: str | None = None
        self._hits = 0
        self._misses = 0

    # -- introspection ------------------------------------------------------

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def snapshot(self) -> dict[str, Any]:
        return {
            "ttl_seconds": self._ttl,
            "last_error": self._last_error,
            "hits": self._hits,
            "misses": self._misses,
            "tables": dict(self._meta),
        }

    def is_ready(self) -> bool:
        return len(self._meta) == len(self._table_names)

    # -- loading ------------------------------------------------------------

    async def _load_table(self, name: str) -> tuple[list[str], list[list[Any]]]:
        async with self._pool.connection() as conn:
            cur = conn.cursor()
            await cur.execute(f"SELECT * FROM {name}")
            rows = await cur.fetchall()
            columns = [d[0].lower() for d in cur.description]
        return columns, [[_normalize(v) for v in row] for row in rows]

    def _store(self, name: str, columns: list[str], rows: list[list[Any]]) -> None:
        self._cache[name] = (columns, rows)
        self._meta[name] = {
            "rows": len(rows),
            "loaded_at": dt.datetime.now().isoformat(timespec="seconds"),
        }

    async def get_table(self, name: str) -> tuple[list[str], list[list[Any]]]:
        hit = self._cache.get(name)
        if hit is not None:
            self._hits += 1
            return hit
        self._misses += 1
        async with self._lock:
            hit = self._cache.get(name)  # double-check under the lock
            if hit is not None:
                return hit
            columns, rows = await self._load_table(name)
            self._store(name, columns, rows)
            logger.info("cache miss: loaded %s (%d rows)", name, len(rows))
            return columns, rows

    async def refresh_all(self) -> dict[str, Any]:
        fresh: dict[str, tuple[list[str], list[list[Any]]]] = {}
        errors: list[str] = []
        for name in self._table_names:
            try:
                fresh[name] = await self._load_table(name)
            except Exception as exc:  # noqa: BLE001 - keep serving stale data
                errors.append(f"{name}: {exc}")
                logger.warning("cache refresh failed for %s: %s", name, exc)
        if fresh:
            async with self._lock:
                for name, (columns, rows) in fresh.items():
                    self._store(name, columns, rows)
        self._last_error = "; ".join(errors) if errors else None
        return {"reloaded": len(fresh), "errors": errors}

    # -- background warm refresh --------------------------------------------

    async def refresh_loop(self, interval_seconds: float) -> None:
        """Keep the tables warm so user requests never hit Netezza."""
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                result = await self.refresh_all()
                if result["errors"]:
                    logger.warning("background refresh: %s", result["errors"])
                else:
                    logger.info("background refresh: %d tables reloaded", result["reloaded"])
            except Exception:  # noqa: BLE001
                logger.exception("background cache refresh crashed")