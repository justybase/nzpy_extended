"""Repository interface for the MIS data source.

Implementations:
    CachedMISRepository — ordinary Netezza tables cached in memory
    FakeRepository (tests) — small in-memory datasets, no database needed
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MISRepository(ABC):
    """Read access to the MIS_* tables plus cache lifecycle hooks."""

    @abstractmethod
    async def get_table(self, name: str) -> tuple[list[str], list[list[Any]]]:
        """Return (column_names, rows) for a table, normalized to plain
        Python types (dates -> ISO strings, Decimal -> float)."""

    async def get_table_slice(self, name: str, column: str,
                              value: Any) -> tuple[list[str], list[list[Any]]]:
        """Return an equality-filtered table slice.

        In-memory repositories inherit this safe reference implementation;
        database repositories can push the predicate down to Netezza.
        """
        columns, rows = await self.get_table(name)
        idx = columns.index(column)
        expected = str(value)[:10]
        return columns, [row for row in rows if str(row[idx])[:10] == expected]

    @abstractmethod
    async def refresh_all(self) -> dict[str, Any]:
        """Reload every table. Failed tables keep their previous contents;
        errors are reported in the returned dict."""

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """Status payload (rows/loaded_at per table, hit/miss counters, TTL)."""

    @abstractmethod
    def is_ready(self) -> bool:
        """True when all configured tables are loaded."""

    @property
    @abstractmethod
    def last_error(self) -> str | None:
        """Last refresh error, if any."""
