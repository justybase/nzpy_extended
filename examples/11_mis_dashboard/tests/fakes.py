"""Shared test doubles — no database required.

FakeRepository implements the same MISRepository interface as
CachedMISRepository (app/repositories/base.py) over plain in-memory tables:
    {table_name: (column_names, rows)}
"""

from __future__ import annotations

from typing import Any

from app.core.cache_types import DatasetVersion
from app.repositories.base import MISRepository


class FakeRepository(MISRepository):
    def __init__(self, tables: dict[str, tuple[list[str], list[list[Any]]]]) -> None:
        self._tables = tables
        self.load_count = 0  # get_table() calls — used to assert cache behaviour

    async def get_table(self, name: str):
        self.load_count += 1
        cols, rows = self._tables[name]
        return cols, [list(r) for r in rows]

    async def refresh_all(self, generation: DatasetVersion | None = None) -> dict[str, Any]:
        return {"reloaded": len(self._tables), "errors": []}

    def snapshot(self) -> dict[str, Any]:
        return {
            "ttl_seconds": 0, "last_error": None, "hits": 0, "misses": 0,
            "tables": {k: {"rows": len(v[1]), "loaded_at": "2025-01-01T00:00:00"}
                       for k, v in self._tables.items()},
        }

    def is_ready(self) -> bool:
        return True

    @property
    def last_error(self) -> str | None:
        return None
