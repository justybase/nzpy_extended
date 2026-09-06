"""Version-aware refresh coordination for the application data cache.

The dashboard reads ordinary MIS tables from Netezza into a process-local
cache.  A small ordinary control table records the latest successfully
published ETL version.  Polling that table avoids re-reading the datasets when
the published version has not changed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from typing import Any, Callable, cast

from app.core.cache_types import DatasetVersion

logger = logging.getLogger("mis.cache.coordinator")


class DatasetVersionStore:
    """Read the latest published version without using the data cache."""

    def __init__(self, pool: Any, table_name: str = "MIS_CONTROL_DATASET_LOAD") -> None:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) is None:
            raise ValueError(f"Invalid control table identifier: {table_name}")
        self._pool = pool
        self._table_name = table_name

    async def latest(self, dataset_name: str) -> DatasetVersion | None:
        async with self._pool.connection() as conn:
            cur = conn.cursor()
            await cur.execute(
                f"SELECT dataset_name, version_no, load_id, source_max_date, "
                f"published_at, status, row_count, checksum "
                f"FROM {self._table_name} "
                "WHERE dataset_name = ? AND status = ? "
                "ORDER BY version_no DESC LIMIT 1",
                (dataset_name, "PUBLISHED"),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        source_watermark = row[3]
        if isinstance(source_watermark, (dt.date, dt.datetime)):
            source_watermark = source_watermark.isoformat()
        return DatasetVersion(
            dataset_name=str(row[0]),
            version_no=int(row[1]),
            load_id=str(row[2]),
            source_watermark=str(source_watermark) if source_watermark is not None else None,
            published_at=str(row[4]),
            status=str(row[5]),
            row_count=int(row[6]) if row[6] is not None else None,
            checksum=str(row[7]) if row[7] is not None else None,
        )


class CacheCoordinator:
    """Coordinate table refreshes and invalidation of all derived caches."""

    def __init__(
        self,
        repository: Any,
        pool: Any,
        report_service: Any,
        people_service: Any,
        ledger_service: Any,
        temporal_service: Any,
        dataset_name: str = "MIS_DASHBOARD",
        control_table_name: str = "MIS_CONTROL_DATASET_LOAD",
        poll_seconds: int = 300,
        max_unconfirmed_seconds: int = 86_400,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._store = DatasetVersionStore(pool, control_table_name)
        self._report_service = report_service
        self._people_service = people_service
        self._ledger_service = ledger_service
        self._temporal_service = temporal_service
        self._dataset_name = dataset_name
        self._poll_seconds = poll_seconds
        self._max_unconfirmed_seconds = max_unconfirmed_seconds
        self._clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self._refresh_lock = asyncio.Lock()
        self._control_version: DatasetVersion | None = None
        self._refreshed_version: DatasetVersion | None = None
        self._last_control_check: dt.datetime | None = None
        self._last_successful_control_check: dt.datetime | None = None
        self._last_refresh: dt.datetime | None = None
        self._last_refresh_reason: str | None = None
        self._control_error: str | None = None
        self._refresh_error: str | None = None

    @property
    def dataset_name(self) -> str:
        return self._dataset_name

    async def initialize(self) -> dict[str, Any]:
        """Restore a durable generation or warm the cache from Netezza."""
        restored: dict[str, Any] = {}
        restore_persisted = getattr(self._repository, "restore_persisted", None)
        if restore_persisted is not None:
            restored = cast(dict[str, Any], await restore_persisted())
            if restored.get("restored"):
                restored_generation = restored.get("generation")
                if isinstance(restored_generation, DatasetVersion):
                    self._refreshed_version = restored_generation
                committed_at = restored.get("committed_at")
                if committed_at:
                    try:
                        self._last_refresh = dt.datetime.fromisoformat(str(committed_at))
                        self._last_refresh_reason = "disk_restore"
                    except ValueError:
                        logger.warning("invalid persisted cache timestamp: %s", committed_at)

        control_read_ok = False
        version: DatasetVersion | None = None
        result: dict[str, Any]
        try:
            version = await self._read_control()
            checked_at = self._clock()
            self._last_control_check = checked_at
            self._last_successful_control_check = checked_at
            self._control_error = None
            control_read_ok = True
        except Exception as exc:  # noqa: BLE001 - startup remains usable with warning
            self._record_control_error(exc)
        if control_read_ok:
            self._control_version = version
            restored_generation = restored.get("generation")
            if restored.get("restored") and (
                isinstance(restored_generation, DatasetVersion)
                and version is not None
                and restored_generation.token == version.token
            ):
                result = {
                    "restored": True,
                    "reloaded": 0,
                    "errors": [],
                    "committed": True,
                    "persistent_committed": True,
                }
            else:
                result = await self._refresh_tables("startup", version)
        elif restored.get("restored"):
            result = {
                "restored": True,
                "reloaded": 0,
                "errors": [],
                "committed": True,
                "persistent_committed": True,
            }
        else:
            result = await self._refresh_tables("startup", None)
        self._publish_status()
        return result

    async def refresh(self, reason: str = "manual") -> dict[str, Any]:
        """Force a table refresh, then clear all derived application caches."""
        generation: DatasetVersion | None = None
        try:
            generation = await self._read_control()
            self._control_version = generation
            self._last_control_check = self._clock()
            self._last_successful_control_check = self._last_control_check
            self._control_error = None
        except Exception as exc:  # noqa: BLE001 - data refresh can still be attempted
            self._record_control_error(exc)
        result = await self._refresh_tables(reason, generation)
        self._publish_status()
        return result

    async def poll_once(self) -> dict[str, Any]:
        """Check the control table and refresh only after a version change."""
        try:
            version = await self._read_control()
            checked_at = self._clock()
            self._last_control_check = checked_at
            self._last_successful_control_check = checked_at
            self._control_error = None
        except Exception as exc:  # noqa: BLE001 - retain the last good data
            self._record_control_error(exc)
            self._publish_status()
            return {"changed": False, "errors": [str(exc)], "reloaded": 0}

        self._control_version = version
        if version is None:
            self._publish_status()
            return {"changed": False, "errors": [], "reloaded": 0,
                    "reason": "no_published_version"}
        if self._refreshed_version is not None and version.token == self._refreshed_version.token:
            self._publish_status()
            return {"changed": False, "errors": [], "reloaded": 0,
                    "version": version.version_no}

        result = await self._refresh_tables(
            f"dataset version {version.version_no} ({version.load_id})", version)
        result["changed"] = True
        result["version"] = version.version_no
        self._publish_status()
        return result

    async def refresh_loop(self, interval_seconds: float | None = None) -> None:
        """Poll the tiny control table until application shutdown."""
        interval = interval_seconds or self._poll_seconds
        while True:
            await asyncio.sleep(interval)
            try:
                result = await self.poll_once()
                if result.get("errors"):
                    logger.warning("cache control poll: %s", result["errors"])
                elif result.get("changed"):
                    logger.info("cache refresh after published version %s", result.get("version"))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the monitor alive
                logger.exception("cache control loop failed")

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        reference = self._last_successful_control_check or self._last_refresh
        unconfirmed_age = int((now - reference).total_seconds()) if reference else None
        cache_age = int((now - self._last_refresh).total_seconds()) if self._last_refresh else None
        version_mismatch = (
            self._control_version is not None
            and (self._refreshed_version is None
                 or self._control_version.token != self._refreshed_version.token)
        )
        stale = bool(
            version_mismatch
            or self._refresh_error
            or self._control_error
            or (unconfirmed_age is not None and unconfirmed_age > self._max_unconfirmed_seconds)
        )
        return {
            "dataset_name": self._dataset_name,
            "dataset_version": self._control_version.version_no if self._control_version else None,
            "load_id": self._control_version.load_id if self._control_version else None,
            "source_watermark": self._control_version.source_watermark if self._control_version else None,
            "published_at": self._control_version.published_at if self._control_version else None,
            "refreshed_version": self._refreshed_version.version_no if self._refreshed_version else None,
            "control_checked_at": self._last_control_check.isoformat() if self._last_control_check else None,
            "control_check_ok": self._control_error is None and self._last_control_check is not None,
            "control_error": self._control_error,
            "cache_age_seconds": cache_age,
            "unconfirmed_age_seconds": unconfirmed_age,
            "max_unconfirmed_seconds": self._max_unconfirmed_seconds,
            "stale": stale,
            "last_refresh_at": self._last_refresh.isoformat() if self._last_refresh else None,
            "last_refresh_reason": self._last_refresh_reason,
            "refresh_error": self._refresh_error,
        }

    async def _read_control(self) -> DatasetVersion | None:
        return await self._store.latest(self._dataset_name)

    async def _refresh_tables(
        self,
        reason: str,
        generation: DatasetVersion | None,
    ) -> dict[str, Any]:
        async with self._refresh_lock:
            try:
                result = cast(
                    dict[str, Any],
                    await self._repository.refresh_all(generation),
                )
            except Exception as exc:  # noqa: BLE001 - retain the last good data
                result = {"reloaded": 0, "errors": [str(exc)], "committed": False}
            raw_errors: Any = result.get("errors", [])
            errors = [str(error) for error in raw_errors]
            if errors:
                self._refresh_error = "; ".join(errors)
                logger.warning("cache refresh failed (%s): %s", reason, errors)
                return result

            self._refresh_error = None
            self._last_refresh = self._clock()
            self._last_refresh_reason = reason
            if generation is not None:
                self._refreshed_version = generation
            else:
                # A successful unversioned refresh has no identity that can
                # be reported as the active ETL generation.
                self._refreshed_version = None
            self._clear_derived_caches()
            result["derived_caches_cleared"] = True
            return result

    def _clear_derived_caches(self) -> None:
        for service in (self._report_service, self._people_service,
                        self._ledger_service, self._temporal_service):
            clear_cache = getattr(service, "clear_cache", None)
            if clear_cache is not None:
                clear_cache()

    def _record_control_error(self, exc: Exception) -> None:
        self._last_control_check = self._clock()
        self._control_error = str(exc)
        logger.warning("cache control table unavailable: %s", exc)

    def _publish_status(self) -> None:
        set_freshness = getattr(self._repository, "set_freshness", None)
        if set_freshness is not None:
            set_freshness(self.snapshot())
