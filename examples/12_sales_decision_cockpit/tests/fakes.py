"""In-memory repository used by all service tests."""

from __future__ import annotations

from typing import Any

from app.repositories.base import SDCRepository


class FakeRepository(SDCRepository):
    def __init__(self, tables: dict[str, tuple[list[str], list[list[Any]]]]) -> None:
        self.tables = tables
        self.load_count = 0

    async def get_table(self, name: str):
        self.load_count += 1
        columns, rows = self.tables[name]
        return columns, [list(row) for row in rows]

    async def refresh_all(self):
        return {"reloaded": len(self.tables), "errors": [], "committed": True}

    def snapshot(self):
        return {
            "cache_mode": "fake",
            "ttl_seconds": 0,
            "hits": 0,
            "misses": 0,
            "last_error": None,
            "tables": {
                name: {"rows": len(rows), "loaded_at": "2026-09-06T00:00:00+00:00"}
                for name, (_columns, rows) in self.tables.items()
            },
        }

    def is_ready(self) -> bool:
        return True

    @property
    def last_error(self) -> str | None:
        return None
