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
from pathlib import Path
from typing import Any

from cachetools import LRUCache

from app.core.cache_types import DatasetVersion
from app.repositories.base import MISRepository
from app.repositories.sqlite_snapshot import SQLiteSnapshotStore

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
        sqlite_path: Path | None = None,
    ) -> None:
        self._pool = pool
        self._table_names = list(table_names)
        self._lazy_table_names = set(lazy_table_names or ())
        self._eager_table_names = [
            name for name in self._table_names if name not in self._lazy_table_names
        ]
        self._snapshot_store = SQLiteSnapshotStore(sqlite_path)
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
        # The RAM generation and the generation represented by the active
        # SQLite file can diverge when persistence is disabled or a stage/
        # activation fails.  Keep them separate so status never overclaims
        # what will survive a restart.
        self._generation: DatasetVersion | None = None
        self._generation_id: str | None = None
        self._persistent_generation: DatasetVersion | None = None
        self._persistent_generation_id: str | None = None
        self._persistent_snapshot_at: str | None = None
        self._persistent_error: str | None = None
        self._restored_from_disk = False
        self._lazy_persistent_hits = 0
        self._lazy_persistent_misses = 0

    # -- introspection ------------------------------------------------------

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def snapshot(self) -> dict[str, Any]:
        result = {
            "ttl_seconds": self._ttl,
            "cache_mode": (
                "etl_versioned_sqlite" if self._snapshot_store.enabled
                else "etl_versioned"
            ),
            "last_error": self._last_error,
            "hits": self._hits,
            "misses": self._misses,
            "tables": dict(self._meta),
            "persistent_enabled": self._snapshot_store.enabled,
            "persistent_snapshot_present": bool(
                self._snapshot_store.path and self._snapshot_store.path.is_file()
            ),
            "persistent_snapshot_version": (
                self._persistent_generation.version_no
                if self._persistent_generation else None
            ),
            "persistent_snapshot_load_id": (
                self._persistent_generation.load_id
                if self._persistent_generation else None
            ),
            "persistent_generation_id": self._persistent_generation_id,
            "persistent_snapshot_at": self._persistent_snapshot_at,
            "persistent_error": self._persistent_error,
            "restored_from_disk": self._restored_from_disk,
            "lazy_persistent_hits": self._lazy_persistent_hits,
            "lazy_persistent_misses": self._lazy_persistent_misses,
        }
        result.update(self._freshness)
        return result

    def set_freshness(self, status: dict[str, Any]) -> None:
        """Attach control-table status to the repository status payload."""
        self._freshness = dict(status)

    def is_ready(self) -> bool:
        return set(self._eager_table_names) <= set(self._meta)

    async def restore_persisted(self) -> dict[str, Any]:
        """Restore the last complete eager generation from SQLite."""
        result = await self._snapshot_store.restore(self._eager_table_names)
        if not result.get("restored"):
            reason = result.get("error") or result.get("reason")
            if reason not in (None, "disabled", "missing"):
                self._persistent_error = str(reason)
                logger.warning("persistent cache restore failed: %s", reason)
            return result

        tables = result["tables"]
        async with self._lock:
            replacement: LRUCache[str, tuple[list[str], list[list[Any]]]] = LRUCache(
                maxsize=64
            )
            for name, table in tables.items():
                replacement[name] = table
            self._cache = replacement
            self._meta = dict(result.get("metadata", {}))
            self._slice_cache.clear()
            self._generation = result.get("generation")
            self._generation_id = result.get("generation_id")
            self._persistent_generation = result.get("generation")
            self._persistent_generation_id = result.get("generation_id")
            self._persistent_snapshot_at = result.get("committed_at")
            self._persistent_error = None
            self._restored_from_disk = True
        logger.info(
            "restored persistent cache generation %s",
            self._generation_id or "unknown",
        )
        return result

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
            if (
                self._generation_id is not None
                and self._generation_id == self._persistent_generation_id
            ):
                persisted = await self._snapshot_store.read_lazy_slice(
                    self._persistent_generation_id, name, column, value
                )
                if persisted is not None:
                    self._slice_cache[key] = persisted
                    self._lazy_persistent_hits += 1
                    self._hits += 1
                    return persisted
                self._lazy_persistent_misses += 1
            async with self._pool.connection() as conn:
                cur = conn.cursor()
                await cur.execute(f"SELECT * FROM {name} WHERE {column} = ?", (value,))
                rows = await cur.fetchall()
                columns = [description[0].lower() for description in cur.description]
            result = (columns, [[_normalize(cell) for cell in row] for row in rows])
            self._slice_cache[key] = result
            if (
                self._generation_id is not None
                and self._generation_id == self._persistent_generation_id
            ):
                try:
                    await self._snapshot_store.write_lazy_slice(
                        self._persistent_generation_id, name, column, value, *result
                    )
                except Exception as exc:  # noqa: BLE001 - RAM result remains usable
                    self._persistent_error = str(exc)
                    logger.warning("persistent lazy cache write failed: %s", exc)
            return result

    async def refresh_all(self, generation: DatasetVersion | None = None) -> dict[str, Any]:
        fresh: dict[str, tuple[list[str], list[list[Any]]]] = {}
        errors: list[str] = []
        for name in self._eager_table_names:
            try:
                fresh[name] = await self._load_table(name)
            except Exception as exc:  # noqa: BLE001 - keep serving stale data
                errors.append(f"{name}: {exc}")
                logger.warning("cache refresh failed for %s: %s", name, exc)
        if errors:
            # Do not mix tables from different published versions.
            self._last_error = "; ".join(errors)
            return {"reloaded": 0, "errors": errors, "committed": False}

        staged_path: Path | None = None
        persistent_error: str | None = None
        if self._snapshot_store.enabled and generation is not None:
            try:
                staged_path = await self._snapshot_store.stage_generation(
                    fresh, self._eager_table_names, generation
                )
            except Exception as exc:  # noqa: BLE001 - RAM cache remains usable
                persistent_error = str(exc)
                logger.warning("persistent cache staging failed: %s", exc)

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
                if staged_path is not None and generation is not None:
                    try:
                        await self._snapshot_store.activate(staged_path)
                    except Exception as exc:  # noqa: BLE001 - keep RAM serviceable
                        persistent_error = str(exc)
                        self._persistent_error = persistent_error
                        logger.warning("persistent cache activation failed: %s", exc)
                        await self._snapshot_store.discard(staged_path)
                    else:
                        self._persistent_generation = generation
                        self._persistent_generation_id = generation.generation_id
                        self._persistent_snapshot_at = dt.datetime.now(
                            dt.timezone.utc
                        ).isoformat(timespec="seconds")
                        self._persistent_error = None
                        self._restored_from_disk = False
                elif persistent_error is not None:
                    self._persistent_error = persistent_error
                self._cache = replacement
                self._meta = replacement_meta
                self._slice_cache.clear()
                self._generation = generation
                self._generation_id = generation.generation_id if generation else None
                self._restored_from_disk = False
        self._last_error = None
        return {
            "reloaded": len(fresh),
            "errors": [],
            "committed": True,
            "persistent_committed": staged_path is not None and persistent_error is None,
            "persistent_error": persistent_error,
        }
