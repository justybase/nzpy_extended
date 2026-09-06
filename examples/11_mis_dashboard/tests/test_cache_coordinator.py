"""Unit tests for ETL-version-driven cache refreshes."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.cache_coordinator import CacheCoordinator, DatasetVersionStore  # noqa: E402
from app.core.cache_types import DatasetVersion  # noqa: E402


class FakeCursor:
    def __init__(self, pool: "FakeControlPool") -> None:
        self._pool = pool
        self._row: tuple[Any, ...] | None = None

    async def execute(self, _sql: str, _params: tuple[Any, ...]) -> None:
        self._pool.query_count += 1
        if self._pool.error is not None:
            raise self._pool.error
        self._row = self._pool.row

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class FakeConnection:
    def __init__(self, pool: "FakeControlPool") -> None:
        self._pool = pool

    async def __aenter__(self) -> "FakeConnection":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._pool)


class FakeControlPool:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self.row = row
        self.error: Exception | None = None
        self.query_count = 0

    def connection(self) -> FakeConnection:
        return FakeConnection(self)


class RefreshRepository:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.fail_next = False
        self.status: dict[str, Any] = {}
        self.restore_result: dict[str, Any] = {"restored": False}
        self.received_generations: list[DatasetVersion | None] = []

    async def refresh_all(self, generation: DatasetVersion | None = None) -> dict[str, Any]:
        self.refresh_calls += 1
        self.received_generations.append(generation)
        if self.fail_next:
            self.fail_next = False
            return {"reloaded": 0, "errors": ["MIS_FACT_SALES: unavailable"]}
        return {"reloaded": 3, "errors": []}

    async def restore_persisted(self) -> dict[str, Any]:
        return self.restore_result

    def set_freshness(self, status: dict[str, Any]) -> None:
        self.status = status


class Clearable:
    def __init__(self) -> None:
        self.clear_calls = 0

    def clear_cache(self) -> None:
        self.clear_calls += 1


def control_row(version: int) -> tuple[Any, ...]:
    return (
        "MIS_DASHBOARD", version, f"LOAD-{version}", "2026-08-15",
        f"2026-08-15T06:{version:02d}:00", "PUBLISHED", 100, None,
    )


@pytest.fixture
def clock() -> list[dt.datetime]:
    return [dt.datetime(2026, 8, 15, 7, 0, tzinfo=dt.timezone.utc)]


def make_coordinator(
        pool: FakeControlPool,
        repository: RefreshRepository,
        clock: list[dt.datetime],
) -> tuple[CacheCoordinator, list[Clearable]]:
    services = [Clearable() for _ in range(4)]
    coordinator = CacheCoordinator(
        repository, pool, *services,
        clock=lambda: clock[0],
    )
    return coordinator, services


async def test_unchanged_version_does_not_reload_dataset(
        clock: list[dt.datetime]) -> None:
    pool = FakeControlPool(control_row(1))
    repository = RefreshRepository()
    coordinator, services = make_coordinator(pool, repository, clock)

    await coordinator.initialize()
    result = await coordinator.poll_once()

    assert result["changed"] is False
    assert repository.refresh_calls == 1
    assert pool.query_count == 2  # startup check + one small polling query
    assert all(service.clear_calls == 1 for service in services)
    assert repository.status["dataset_version"] == 1
    assert repository.status["stale"] is False


async def test_matching_persisted_version_skips_source_reload(
        clock: list[dt.datetime]) -> None:
    pool = FakeControlPool(control_row(1))
    repository = RefreshRepository()
    repository.restore_result = {
        "restored": True,
        "generation": DatasetVersion(
            "MIS_DASHBOARD", 1, "LOAD-1", "2026-08-15",
            "2026-08-15T06:01:00", "PUBLISHED",
        ),
        "committed_at": "2026-08-15T06:30:00+00:00",
    }
    coordinator, services = make_coordinator(pool, repository, clock)

    result = await coordinator.initialize()

    assert result["restored"] is True
    assert repository.refresh_calls == 0
    assert all(service.clear_calls == 0 for service in services)


async def test_missing_published_version_refreshes_restored_snapshot(
        clock: list[dt.datetime]) -> None:
    pool = FakeControlPool(control_row(1))
    pool.row = None
    repository = RefreshRepository()
    repository.restore_result = {
        "restored": True,
        "generation": DatasetVersion(
            "MIS_DASHBOARD", 1, "LOAD-1", "2026-08-15",
            "2026-08-15T06:01:00", "PUBLISHED",
        ),
        "committed_at": "2026-08-15T06:30:00+00:00",
    }
    coordinator, _services = make_coordinator(pool, repository, clock)

    result = await coordinator.initialize()

    assert result["reloaded"] == 3
    assert repository.refresh_calls == 1
    assert repository.received_generations == [None]


async def test_older_persisted_version_refreshes_with_control_generation(
        clock: list[dt.datetime]) -> None:
    pool = FakeControlPool(control_row(2))
    repository = RefreshRepository()
    repository.restore_result = {
        "restored": True,
        "generation": DatasetVersion(
            "MIS_DASHBOARD", 1, "LOAD-1", "2026-08-15",
            "2026-08-15T06:01:00", "PUBLISHED",
        ),
        "committed_at": "2026-08-15T06:30:00+00:00",
    }
    coordinator, _services = make_coordinator(pool, repository, clock)

    result = await coordinator.initialize()

    assert result["reloaded"] == 3
    assert repository.refresh_calls == 1
    assert repository.received_generations[0] is not None
    assert repository.received_generations[0].version_no == 2


async def test_control_failure_keeps_restored_snapshot(
        clock: list[dt.datetime]) -> None:
    pool = FakeControlPool(control_row(1))
    pool.error = RuntimeError("control table unavailable")
    repository = RefreshRepository()
    repository.restore_result = {
        "restored": True,
        "generation": DatasetVersion(
            "MIS_DASHBOARD", 1, "LOAD-1", "2026-08-15",
            "2026-08-15T06:01:00", "PUBLISHED",
        ),
        "committed_at": "2026-08-15T06:30:00+00:00",
    }
    coordinator, _services = make_coordinator(pool, repository, clock)

    result = await coordinator.initialize()

    assert result["restored"] is True
    assert repository.refresh_calls == 0
    assert repository.status["stale"] is True


async def test_new_published_version_reloads_and_clears_derived_caches(
        clock: list[dt.datetime]) -> None:
    pool = FakeControlPool(control_row(1))
    repository = RefreshRepository()
    coordinator, services = make_coordinator(pool, repository, clock)
    await coordinator.initialize()

    pool.row = control_row(2)
    result = await coordinator.poll_once()

    assert result["changed"] is True
    assert result["version"] == 2
    assert repository.refresh_calls == 2
    assert all(service.clear_calls == 2 for service in services)
    assert repository.status["refreshed_version"] == 2


async def test_failed_refresh_keeps_previous_version_and_retries(
        clock: list[dt.datetime]) -> None:
    pool = FakeControlPool(control_row(1))
    repository = RefreshRepository()
    coordinator, _services = make_coordinator(pool, repository, clock)
    await coordinator.initialize()

    pool.row = control_row(2)
    repository.fail_next = True
    failed = await coordinator.poll_once()

    assert failed["errors"]
    assert repository.status["refreshed_version"] == 1
    assert repository.status["stale"] is True

    retried = await coordinator.poll_once()
    assert retried["changed"] is True
    assert retried["errors"] == []
    assert repository.status["refreshed_version"] == 2
    assert repository.status["stale"] is False


async def test_control_failure_marks_cache_stale_after_24_hours(
        clock: list[dt.datetime]) -> None:
    pool = FakeControlPool(control_row(1))
    repository = RefreshRepository()
    coordinator, _services = make_coordinator(pool, repository, clock)
    await coordinator.initialize()

    clock[0] += dt.timedelta(seconds=86_401)
    pool.error = RuntimeError("control table unavailable")
    result = await coordinator.poll_once()

    assert result["errors"] == ["control table unavailable"]
    assert repository.status["control_check_ok"] is False
    assert repository.status["unconfirmed_age_seconds"] == 86_401
    assert repository.status["max_unconfirmed_seconds"] == 86_400
    assert repository.status["stale"] is True


def test_control_table_name_is_a_safe_sql_identifier() -> None:
    with pytest.raises(ValueError, match="Invalid control table identifier"):
        DatasetVersionStore(object(), "MIS_CONTROL_DATASET_LOAD; DROP TABLE MIS_FACT_SALES")
