"""Unit tests for SchemaService caching/escaping — no database required."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.schema_service import SchemaService, escape_like  # noqa: E402


class FakeMeta:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    async def get_schemas(self) -> list[str]:
        self._count("schemas")
        return ["ADMIN", "DEV"]

    async def get_tables(self, schema: Any = None) -> list[dict[str, Any]]:
        self._count(f"tables:{schema}")
        return [{"schema": schema or "ADMIN", "table_name": "T1",
                 "objtype": "TABLE"}]

    async def get_views(self, schema: Any = None) -> list[dict[str, Any]]:
        self._count("views")
        return []

    async def get_procedures(self, schema: Any = None) -> list[dict[str, Any]]:
        self._count("procs")
        return []

    async def get_columns(self, table: str, schema: Any = None) -> list[dict[str, Any]]:
        self._count(f"cols:{schema}.{table}")
        return [{"column_name": "ID", "data_type": "INTEGER"}]

    async def get_distribution_key(self, table: str, schema: Any = None) -> list[str]:
        return ["ID"]

    async def get_table_sizes(self, schema: Any = None,
                              table_pattern: Any = None) -> list[dict[str, Any]]:
        assert "''" in (table_pattern or "") or "'" not in (table_pattern or "")
        return [{"table_name": "T1", "size_mb": 7}]

    async def search_objects(self, pattern: str, schema: Any = None) -> list[dict[str, Any]]:
        assert "''" in pattern or "'" not in pattern
        return []


class FakeConn:
    def __init__(self, meta: FakeMeta) -> None:
        self.meta = meta

    async def __aenter__(self) -> "FakeConn":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class FakePool:
    def __init__(self, meta: FakeMeta) -> None:
        self._meta = meta

    def connection(self) -> FakeConn:
        return FakeConn(self._meta)


def test_escape_like_doubles_quotes() -> None:
    assert escape_like("o'clock") == "o''clock"


async def test_schemas_cached() -> None:
    meta = FakeMeta()
    svc = SchemaService(lambda: FakePool(meta), ttl_seconds=60)
    assert await svc.get_schemas() == ["ADMIN", "DEV"]
    assert await svc.get_schemas() == ["ADMIN", "DEV"]
    assert meta.calls["schemas"] == 1


async def test_columns_lru_cached() -> None:
    meta = FakeMeta()
    svc = SchemaService(lambda: FakePool(meta), ttl_seconds=60)
    await svc.get_columns("t1", schema="ADMIN")
    await svc.get_columns("T1", schema="admin")
    assert meta.calls.get("cols:ADMIN.t1", 0) + meta.calls.get("cols:admin.T1", 0) == 1


async def test_table_detail_escapes_pattern() -> None:
    meta = FakeMeta()
    svc = SchemaService(lambda: FakePool(meta))
    detail = await svc.table_detail("o'clock", schema="ADMIN")
    assert detail["distribution_key"] == ["ID"]
    assert detail["size_mb"] is None  # pattern table name did not match T1


async def test_invalidate_clears_cache() -> None:
    meta = FakeMeta()
    svc = SchemaService(lambda: FakePool(meta), ttl_seconds=60)
    await svc.get_schemas()
    svc.invalidate()
    await svc.get_schemas()
    assert meta.calls["schemas"] == 2
