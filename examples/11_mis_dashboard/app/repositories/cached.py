"""Cached ordinary Netezza tables in process memory.

The table cache is invalidated by ``CacheCoordinator`` when the ETL publishes
a new dataset version. It is deliberately not refreshed on a timer: the
application should not re-read large datasets when the published version is
unchanged. Report payloads keep their own short TTL in ``ReportService``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from decimal import Decimal
from typing import Any

from cachetools import LRUCache

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
        ttl_seconds: int = 86_400,
        lazy_table_names: set[str] | None = None,
    ) -> None:
        self._pool = pool
        self._table_names = list(table_names)
        self._lazy_table_names = set(lazy_table_names or ())
        # Retention is controlled by the ETL version and explicit refreshes.
        # Keep the old status field for API compatibility; the bounded LRU is
        # the memory guard, not a timer that causes database reads.
        self._ttl = ttl_seconds
        self._cache: LRUCache[str, tuple[list[str], list[list[Any]]]] = LRUCache(maxsize=64)
        self._slice_cache: LRUCache[tuple[str, str, str], tuple[list[str], list[list[Any]]]] = LRUCache(maxsize=2048)
        self._lock = asyncio.Lock()
        self._meta: dict[str, dict[str, Any]] = {}
        self._last_error: str | None = None
        self._freshness: dict[str, Any] = {}
        self._hits = 0
        self._misses = 0

    # -- introspection ------------------------------------------------------

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def snapshot(self) -> dict[str, Any]:
        result = {
            "ttl_seconds": self._ttl,
            "cache_mode": "etl_versioned",
            "last_error": self._last_error,
            "hits": self._hits,
            "misses": self._misses,
            "tables": dict(self._meta),
        }
        result.update(self._freshness)
        return result

    def set_freshness(self, status: dict[str, Any]) -> None:
        """Attach control-table status to the repository status payload."""
        self._freshness = dict(status)

    def is_ready(self) -> bool:
        eager = set(self._table_names) - self._lazy_table_names
        return eager <= set(self._meta)

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

    @staticmethod
    def _metadata(name: str, rows: list[list[Any]]) -> dict[str, Any]:
        return {
            "rows": len(rows),
            "loaded_at": dt.datetime.now().isoformat(timespec="seconds"),
        }

    async def get_table(self, name: str) -> tuple[list[str], list[list[Any]]]:
        hit: tuple[list[str], list[list[Any]]] | None = self._cache.get(name)
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

    async def get_table_slice(self, name: str, column: str,
                              value: Any) -> tuple[list[str], list[list[Any]]]:
        if name not in self._lazy_table_names:
            return await super().get_table_slice(name, column, value)
        key = (name, column, str(value))
        hit: tuple[list[str], list[list[Any]]] | None = self._slice_cache.get(key)
        if hit is not None:
            self._hits += 1
            return hit
        self._misses += 1
        async with self._lock:
            hit = self._slice_cache.get(key)
            if hit is not None:
                return hit
            async with self._pool.connection() as conn:
                cur = conn.cursor()
                await cur.execute(f"SELECT * FROM {name} WHERE {column} = ?", (value,))
                rows = await cur.fetchall()
                columns = [description[0].lower() for description in cur.description]
            result = (columns, [[_normalize(cell) for cell in row] for row in rows])
            self._slice_cache[key] = result
            return result

    async def refresh_all(self) -> dict[str, Any]:
        fresh: dict[str, tuple[list[str], list[list[Any]]]] = {}
        errors: list[str] = []
        for name in self._table_names:
            if name in self._lazy_table_names:
                continue
            try:
                fresh[name] = await self._load_table(name)
            except Exception as exc:  # noqa: BLE001 - keep serving stale data
                errors.append(f"{name}: {exc}")
                logger.warning("cache refresh failed for %s: %s", name, exc)
        if errors:
            # Do not mix tables from different published versions.
            self._last_error = "; ".join(errors)
            return {"reloaded": 0, "errors": errors, "committed": False}
        if fresh:
            # Build a complete replacement cache first. Assigning the two
            # dictionaries while holding the lock makes the committed table
            # set visible as one generation to subsequent readers.
            replacement: LRUCache[str, tuple[list[str], list[list[Any]]]] = LRUCache(maxsize=64)
            replacement_meta: dict[str, dict[str, Any]] = {}
            for name, (columns, rows) in fresh.items():
                replacement[name] = (columns, rows)
                replacement_meta[name] = self._metadata(name, rows)
            async with self._lock:
                self._cache = replacement
                self._meta = replacement_meta
                self._slice_cache.clear()
        self._last_error = None
        return {"reloaded": len(fresh), "errors": [], "committed": True}
