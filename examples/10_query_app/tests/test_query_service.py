"""Unit tests for QueryService — no database required (fake pool/cursor)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.query_service import QueryService, build_columns  # noqa: E402
from app.services.result_session_service import ResultSessionManager  # noqa: E402


class FakeCursor:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self._rows = rows or [(1, "a"), (2, "b")]
        self.description = (("id",), ("name",))
        self.rowcount = len(self._rows)
        self.executed: list[str] = []
        self._position = 0

    def get_schema_table(self) -> list[dict[str, Any]]:
        return [
            {"ColumnName": "id", "DataType": int, "ColumnOrdinal": 1},
            {"ColumnName": "name", "DataType": str, "ColumnOrdinal": 2},
        ]

    async def execute(self, sql: str, timeout: Any = None) -> None:
        self.executed.append(sql)
        self._position = 0
        await asyncio.sleep(0)

    async def fetchmany(self, size: int | None = None) -> list[tuple]:
        size = size or len(self._rows)
        rows = self._rows[self._position:self._position + size]
        self._position += len(rows)
        return rows

    async def fetchall(self) -> list[tuple]:
        return self._rows

    async def close(self) -> None:
        return None


class FakeConn:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.cancelled = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    async def cancel(self) -> None:
        self.cancelled = True


class FakeConnCtx:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> None:
        return None


class FakePool:
    def __init__(self, cursor: FakeCursor) -> None:
        self._conn = FakeConn(cursor)

    def connection(self) -> FakeConnCtx:
        return FakeConnCtx(self._conn)


async def test_execute_returns_columns_rows_count() -> None:
    svc = QueryService(lambda: FakePool(FakeCursor()))
    cols, rows, count, elapsed = await svc.execute("q1", "SELECT 1", 5.0)
    assert [c["ColumnName"] for c in cols] == ["id", "name"]
    assert cols[0]["DataType"] == "int"  # type objects mapped to names
    assert rows == [[1, "a"], [2, "b"]]
    assert count == 2
    assert elapsed >= 0
    assert "q1" not in svc.active  # registry cleaned up


async def test_request_cancel_unknown_id() -> None:
    svc = QueryService(lambda: FakePool(FakeCursor()))
    assert svc.request_cancel("missing") is False


async def test_request_cancel_flags_entry() -> None:
    svc = QueryService(lambda: FakePool(FakeCursor()))
    svc.active["q9"] = {"cancelled": False}
    assert svc.request_cancel("q9") is True
    assert svc.active["q9"]["cancelled"] is True


async def test_start_streams_a_disk_backed_result_session(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    async def emit(event: dict[str, Any]) -> None:
        events.append(event)

    sessions = ResultSessionManager(tmp_path, ttl_seconds=600, default_page_size=10)
    svc = QueryService(lambda: FakePool(FakeCursor([(1, "a"), (2, "b")])), sessions, result_limit=10, chunk_size=1)
    task = await svc.start("q-stream", "SELECT 1;", mode="script", cursor_offset=None, selection=None, database=None, timeout=5, emit=emit)
    await task
    created = next(event for event in events if event["type"] == "session-created")
    page = sessions.page(created["sessionId"])
    assert page["rows"] == [[1, "a"], [2, "b"]]
    assert [event["type"] for event in events][-1] == "batch-complete"


def test_build_columns_falls_back_to_description() -> None:
    class NoSchema:
        description = (("x",),)

    assert build_columns(NoSchema()) == [
        {"ColumnName": "x", "DataType": "unknown", "ColumnOrdinal": 1}
    ]


def test_build_columns_empty_without_metadata() -> None:
    class Bare:
        description = None

        def get_schema_table(self) -> None:
            return None

    assert build_columns(Bare()) == []
