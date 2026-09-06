"""Repository contract used by services and database-free tests."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SDCRepository(ABC):
    @abstractmethod
    async def get_table(self, name: str) -> tuple[list[str], list[list[Any]]]:
        """Return normalized column names and rows for one SDC table."""

    @abstractmethod
    async def refresh_all(self) -> dict[str, Any]:
        """Replace the cache with one complete table generation."""

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """Return cache and freshness information."""

    @abstractmethod
    def is_ready(self) -> bool:
        """Return true when every configured table is available."""

    @property
    @abstractmethod
    def last_error(self) -> str | None:
        """Return the last refresh error, if any."""
