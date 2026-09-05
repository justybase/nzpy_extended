"""
ReportService — orchestration layer between the API routes and the report
definitions in app.services.reporting.

Responsibilities:
    - resolve report payloads (KPIs + synthetic + analytic + charts)
    - resolve drill-down payloads
    - cache computed payloads in a TTLCache so repeated requests / filter
      changes never recompute aggregations (and never touch Netezza)
    - expose registry metadata and cache status to the routes
"""

from __future__ import annotations

from typing import Any

from cachetools import TTLCache

from app.repositories.base import MISRepository
from app.services import reporting


class ReportService:
    def __init__(self, repository: MISRepository, ttl: int = 120, maxsize: int = 512) -> None:
        self._repository = repository
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)

    # -- report payloads ----------------------------------------------------

    async def build(self, report_id: str, from_: str | None, to: str | None,
                    dim: str | None) -> dict[str, Any]:
        key = ("report", report_id, from_, to, dim)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        payload = await reporting.build(report_id, self._repository, from_, to, dim)
        self._cache[key] = payload
        return payload

    async def drill(self, report_id: str, target: str, key: str,
                    from_: str | None, to: str | None) -> dict[str, Any]:
        cache_key = ("drill", report_id, target, key, from_, to)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        payload = await reporting.drill(report_id, self._repository, from_, to, target, key)
        self._cache[cache_key] = payload
        return payload

    # -- metadata -----------------------------------------------------------

    async def available_months(self) -> list[str]:
        return await reporting.available_months(self._repository)

    def report_list(self) -> list[dict[str, str]]:
        return [{"id": rid, "title": spec["title"]} for rid, spec in reporting.REGISTRY.items()]

    def cache_info(self) -> dict[str, Any]:
        return {
            "report_cache_size": len(self._cache),
            "report_cache_ttl": self._cache.ttl,
        }

    def clear_cache(self) -> None:
        self._cache.clear()