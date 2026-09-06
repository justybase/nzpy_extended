"""Bounded process-local cache for ordinary SDC tables."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from decimal import Decimal
from typing import Any

from cachetools import LRUCache

from app.repositories.base import SDCRepository

logger = logging.getLogger("sdc.cache")


def _normalize(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


class CachedSDCRepository(SDCRepository):
    def __init__(self, pool: Any, table_names: list[str], ttl_seconds: int = 86_400) -> None:
        self._pool = pool
        self._table_names = list(table_names)
        self._ttl = ttl_seconds
        self._cache: LRUCache[str, tuple[list[str], list[list[Any]]]] = LRUCache(maxsize=32)
        self._meta: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def snapshot(self) -> dict[str, Any]:
        return {
            "cache_mode": "bounded_process_lru",
            "ttl_seconds": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
            "last_error": self._last_error,
            "tables": dict(self._meta),
            "ready": self.is_ready(),
        }

    def is_ready(self) -> bool:
        return set(self._table_names) <= set(self._cache)

    async def _load_table(self, name: str) -> tuple[list[str], list[list[Any]]]:
        async with self._pool.connection() as conn:
            cur = conn.cursor()
            await cur.execute(f"SELECT * FROM {name}")
            rows = await cur.fetchall()
            columns = [description[0].lower() for description in cur.description]
        return columns, [[_normalize(value) for value in row] for row in rows]

    async def get_table(self, name: str) -> tuple[list[str], list[list[Any]]]:
        hit = self._cache.get(name)
        if hit is not None:
            self._hits += 1
            return hit
        self._misses += 1
        async with self._lock:
            hit = self._cache.get(name)
            if hit is not None:
                self._hits += 1
                return hit
            columns, rows = await self._load_table(name)
            self._cache[name] = (columns, rows)
            self._meta[name] = {
                "rows": len(rows),
                "loaded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            }
            logger.info("loaded %s (%d rows)", name, len(rows))
            return columns, rows

    async def refresh_all(self) -> dict[str, Any]:
        fresh: dict[str, tuple[list[str], list[list[Any]]]] = {}
        errors: list[str] = []
        for name in self._table_names:
            try:
                fresh[name] = await self._load_table(name)
            except Exception as exc:  # noqa: BLE001 - cache remains usable
                errors.append(f"{name}: {exc}")
        if errors:
            self._last_error = "; ".join(errors)
            return {"reloaded": 0, "errors": errors, "committed": False}
        async with self._lock:
            replacement: LRUCache[str, tuple[list[str], list[list[Any]]]] = LRUCache(maxsize=32)
            metadata: dict[str, dict[str, Any]] = {}
            for name, table in fresh.items():
                replacement[name] = table
                metadata[name] = {
                    "rows": len(table[1]),
                    "loaded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                }
            self._cache = replacement
            self._meta = metadata
            self._last_error = None
        return {"reloaded": len(fresh), "errors": [], "committed": True}
