"""Tests for the durable SQLite cache generation."""

from __future__ import annotations

import asyncio
import re
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.cache_types import DatasetVersion  # noqa: E402
from app.repositories.cached import CachedMISRepository  # noqa: E402
from app.repositories.sqlite_snapshot import SQLiteSnapshotStore  # noqa: E402


def generation(version: int = 1) -> DatasetVersion:
    return DatasetVersion(
        dataset_name="MIS_DASHBOARD",
        version_no=version,
        load_id=f"LOAD-{version}",
        source_watermark="2026-08-15",
        published_at=f"2026-08-15T06:0{version}:00",
        status="PUBLISHED",
        row_count=3,
        checksum=None,
    )


async def activate(
    store: SQLiteSnapshotStore,
    tables: dict[str, tuple[list[str], list[list[Any]]]],
    table_names: list[str],
    version: DatasetVersion,
) -> None:
    staged = await store.stage_generation(tables, table_names, version)
    await store.activate(staged)


async def test_snapshot_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "mis-dashboard.sqlite3"
    store = SQLiteSnapshotStore(path)
    tables = {
        "MIS_DIM_PRODUCT": (
            ["product_id", "product_name", "active"],
            [[1, "Loan", 1], [2, "Savings", 0]],
        ),
        "MIS_DIM_CHANNEL": (
            ["channel_id", "channel_name"],
            [[1, "Branch"]],
        ),
    }

    await activate(store, tables, list(tables), generation())
    restored = await store.restore(list(tables))

    assert restored["restored"] is True
    assert restored["generation"].token == generation().token
    assert restored["tables"] == tables
    assert restored["metadata"]["MIS_DIM_PRODUCT"]["storage"] == "ram+sqlite"


async def test_snapshot_restore_does_not_block_the_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteSnapshotStore(tmp_path / "mis-dashboard.sqlite3")
    started = threading.Event()
    release = threading.Event()

    def blocking_restore(_table_names: list[str]) -> dict[str, Any]:
        started.set()
        release.wait(timeout=1)
        return {"restored": False, "reason": "missing"}

    monkeypatch.setattr(store, "_restore_sync", blocking_restore)
    task = asyncio.create_task(store.restore([]))
    assert await asyncio.to_thread(started.wait, 0.2)
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    assert await task == {"restored": False, "reason": "missing"}


async def test_staged_snapshot_has_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "mis-dashboard.sqlite3"
    store = SQLiteSnapshotStore(path)

    staged = await store.stage_generation(
        {"MIS_DIM_PRODUCT": (["id"], [[1]])},
        ["MIS_DIM_PRODUCT"],
        generation(),
    )
    try:
        assert staged.stat().st_mode & 0o777 == 0o600
    finally:
        await store.discard(staged)


async def test_failed_staging_does_not_replace_active_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "mis-dashboard.sqlite3"
    store = SQLiteSnapshotStore(path)
    table_names = ["MIS_DIM_PRODUCT"]
    original = {"MIS_DIM_PRODUCT": (["id"], [[1]])}
    await activate(store, original, table_names, generation(1))

    with pytest.raises(ValueError, match="row width mismatch"):
        await store.stage_generation(
            {"MIS_DIM_PRODUCT": (["id"], [[2, "unexpected"]])},
            table_names,
            generation(2),
        )

    restored = await store.restore(table_names)
    assert restored["generation"].version_no == 1
    assert restored["tables"] == original


async def test_lazy_slice_round_trip_and_empty_marker(tmp_path: Path) -> None:
    path = tmp_path / "mis-dashboard.sqlite3"
    store = SQLiteSnapshotStore(path)
    tables = {"MIS_DIM_PRODUCT": (["id"], [[1]])}
    version = generation()
    await activate(store, tables, list(tables), version)

    columns = ["snapshot_date", "advisor_id"]
    rows = [["2026-08-15", 7]]
    assert await store.write_lazy_slice(
        version.generation_id,
        "MIS_FACT_PERFORMANCE_SNAPSHOT",
        "snapshot_date",
        "2026-08-15",
        columns,
        rows,
    )
    assert await store.read_lazy_slice(
        version.generation_id,
        "MIS_FACT_PERFORMANCE_SNAPSHOT",
        "snapshot_date",
        "2026-08-15",
    ) == (columns, rows)

    assert await store.write_lazy_slice(
        version.generation_id,
        "MIS_FACT_PERFORMANCE_SNAPSHOT",
        "snapshot_date",
        "2026-08-14",
        columns,
        [],
    )
    assert await store.read_lazy_slice(
        version.generation_id,
        "MIS_FACT_PERFORMANCE_SNAPSHOT",
        "snapshot_date",
        "2026-08-14",
    ) == (columns, [])


async def test_lazy_slice_retention_removes_evicted_rows(tmp_path: Path) -> None:
    path = tmp_path / "mis-dashboard.sqlite3"
    store = SQLiteSnapshotStore(path, max_lazy_slices=1)
    tables = {"MIS_DIM_PRODUCT": (["id"], [[1]])}
    version = generation()
    await activate(store, tables, list(tables), version)

    columns = ["snapshot_date", "advisor_id"]
    await store.write_lazy_slice(
        version.generation_id,
        "MIS_FACT_PERFORMANCE_SNAPSHOT",
        "snapshot_date",
        "2026-08-15",
        columns,
        [["2026-08-15", 7]],
    )
    await store.write_lazy_slice(
        version.generation_id,
        "MIS_FACT_PERFORMANCE_SNAPSHOT",
        "snapshot_date",
        "2026-08-14",
        columns,
        [["2026-08-14", 8]],
    )

    assert await store.read_lazy_slice(
        version.generation_id,
        "MIS_FACT_PERFORMANCE_SNAPSHOT",
        "snapshot_date",
        "2026-08-15",
    ) is None
    assert await store.read_lazy_slice(
        version.generation_id,
        "MIS_FACT_PERFORMANCE_SNAPSHOT",
        "snapshot_date",
        "2026-08-14",
    ) == (columns, [["2026-08-14", 8]])

    # Re-loading the evicted slice must not append a duplicate physical row.
    await store.write_lazy_slice(
        version.generation_id,
        "MIS_FACT_PERFORMANCE_SNAPSHOT",
        "snapshot_date",
        "2026-08-15",
        columns,
        [["2026-08-15", 7]],
    )
    assert await store.read_lazy_slice(
        version.generation_id,
        "MIS_FACT_PERFORMANCE_SNAPSHOT",
        "snapshot_date",
        "2026-08-15",
    ) == (columns, [["2026-08-15", 7]])


class FakeCursor:
    def __init__(self, pool: "FakePool") -> None:
        self._pool = pool
        self.description: list[tuple[str]] | None = None
        self._rows: list[list[Any]] = []

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        match = re.search(r"FROM\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.IGNORECASE)
        if match is None:
            raise AssertionError(sql)
        table = match.group(1)
        columns, rows = self._pool.tables[table]
        self.description = [(column,) for column in columns]
        if " WHERE " in sql.upper():
            value = str(params[0])[:10]
            index = columns.index("snapshot_date")
            self._rows = [row for row in rows if str(row[index])[:10] == value]
        else:
            self._rows = [list(row) for row in rows]
        self._pool.query_count += 1

    async def fetchall(self) -> list[list[Any]]:
        return self._rows


class FakeConnection:
    def __init__(self, pool: "FakePool") -> None:
        self._pool = pool

    async def __aenter__(self) -> "FakeConnection":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._pool)


class FakePool:
    def __init__(self) -> None:
        self.query_count = 0
        self.tables = {
            "MIS_DIM_PRODUCT": (["id"], [[1]]),
            "MIS_FACT_PERFORMANCE_SNAPSHOT": (
                ["snapshot_date", "advisor_id"],
                [["2026-08-15", 7]],
            ),
        }

    def connection(self) -> FakeConnection:
        return FakeConnection(self)


async def test_repository_restores_eager_and_lazy_data_without_source_query(
        tmp_path: Path) -> None:
    pool = FakePool()
    path = tmp_path / "mis-dashboard.sqlite3"
    version = generation()
    repository = CachedMISRepository(
        pool,
        ["MIS_DIM_PRODUCT", "MIS_FACT_PERFORMANCE_SNAPSHOT"],
        lazy_table_names={"MIS_FACT_PERFORMANCE_SNAPSHOT"},
        sqlite_path=path,
    )

    result = await repository.refresh_all(version)
    assert result["persistent_committed"] is True
    assert pool.query_count == 1
    await repository.get_table_slice(
        "MIS_FACT_PERFORMANCE_SNAPSHOT", "snapshot_date", "2026-08-15"
    )
    assert pool.query_count == 2

    restored_repository = CachedMISRepository(
        pool,
        ["MIS_DIM_PRODUCT", "MIS_FACT_PERFORMANCE_SNAPSHOT"],
        lazy_table_names={"MIS_FACT_PERFORMANCE_SNAPSHOT"},
        sqlite_path=path,
    )
    restored = await restored_repository.restore_persisted()
    assert restored["restored"] is True
    assert await restored_repository.get_table("MIS_DIM_PRODUCT") == (["id"], [[1]])
    assert await restored_repository.get_table_slice(
        "MIS_FACT_PERFORMANCE_SNAPSHOT", "snapshot_date", "2026-08-15"
    ) == (["snapshot_date", "advisor_id"], [["2026-08-15", 7]])
    assert pool.query_count == 2
    assert restored_repository.snapshot()["lazy_persistent_hits"] == 1


async def test_snapshot_status_keeps_persisted_generation_after_staging_failure(
        tmp_path: Path) -> None:
    pool = FakePool()
    path = tmp_path / "mis-dashboard.sqlite3"
    repository = CachedMISRepository(
        pool,
        ["MIS_DIM_PRODUCT"],
        sqlite_path=path,
    )
    await repository.refresh_all(generation(1))

    async def fail_stage(*_args: Any) -> Path:
        raise RuntimeError("staging unavailable")

    repository._snapshot_store.stage_generation = fail_stage  # type: ignore[method-assign]
    result = await repository.refresh_all(generation(2))

    assert result["persistent_committed"] is False
    snapshot = repository.snapshot()
    assert snapshot["persistent_snapshot_version"] == 1
    assert snapshot["persistent_generation_id"] == generation(1).generation_id
    assert snapshot["persistent_error"] == "staging unavailable"
