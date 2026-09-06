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

import hashlib
from typing import Any

from cachetools import TTLCache

from app.core.roles import SessionUser
from app.repositories import AsOfMISRepository, ScopedMISRepository
from app.repositories.base import MISRepository
from app.services import reporting


class ReportService:
    def __init__(self, repository: MISRepository, ttl: int = 120, maxsize: int = 512) -> None:
        self._repository = repository
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)

    # -- report payloads ----------------------------------------------------

    def _repo_for(self, user: SessionUser | None, as_of: str | None = None,
                  attribution: str = "historical") -> MISRepository:
        """Row-level role masking: scoped repository for non-analyst users."""
        repository: MISRepository = self._repository
        if as_of is not None:
            repository = AsOfMISRepository(repository, as_of, attribution)
        if user is None or user.is_analyst:
            return repository
        return ScopedMISRepository(repository, user)

    def _snapshot_id(self) -> str:
        snapshot = self._repository.snapshot()
        tables = snapshot.get("tables", {})
        loaded = sorted(str(info.get("loaded_at", "")) for info in tables.values())
        signature = "|".join(loaded)
        return hashlib.sha1(signature.encode(), usedforsecurity=False).hexdigest()[:12]

    def _freshness(self) -> dict[str, Any]:
        snapshot = self._repository.snapshot()
        tables = snapshot.get("tables", {})
        loaded = sorted(str(info.get("loaded_at", "")) for info in tables.values())
        return {
            "loaded_at": max(loaded, default=None),
            "stale": bool(snapshot.get("last_error")),
            "snapshot_id": self._snapshot_id(),
        }

    async def build(self, report_id: str, from_: str | None, to: str | None,
                    dim: str | None,
                    user: SessionUser | None = None,
                    as_of: str | None = None,
                    attribution: str = "historical") -> dict[str, Any]:
        if attribution not in ("historical", "current"):
            raise ValueError("attribution must be 'historical' or 'current'")
        audit: dict[str, Any] | None = None
        if as_of is not None:
            acols, arows = await self._repository.get_table("MIS_AUDIT_SNAPSHOT_LOAD")
            audit = next((dict(zip(acols, row)) for row in arows
                          if str(row[acols.index("snapshot_date")])[:10] == as_of), None)
            if audit is None:
                raise ValueError(f"Snapshot {as_of} is unavailable")
            if (audit["status"] != "PASS"
                    or str(audit["source_max_date"])[:10] != as_of):
                raise ValueError(f"Snapshot {as_of} failed the reporting quality gate")
            # The trend may start earlier, but it may never claim to extend
            # beyond the data cut-off represented by the snapshot.
            to = min(to or as_of[:7], as_of[:7])
            if from_ is not None and from_ > to:
                from_ = to
        scope = user.code if user and not user.is_analyst else "full"
        key = ("report", self._snapshot_id(), scope, report_id, from_, to, dim,
               as_of, attribution)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        payload = await reporting.build(report_id, self._repo_for(user, as_of, attribution),
                                        from_, to, dim)
        payload["freshness"] = self._freshness()
        if as_of is not None and audit is not None:
            data_through = str(audit["source_max_date"])[:10]
            payload["reporting_context"] = {
                "as_of": as_of, "data_through": data_through,
                "load_id": audit["load_id"], "loaded_at": audit["loaded_at"],
                "attribution": attribution,
                "complete": audit["status"] == "PASS" and data_through == as_of,
            }
        self._cache[key] = payload
        return payload

    async def drill(self, report_id: str, target: str, key: str,
                    from_: str | None, to: str | None,
                    user: SessionUser | None = None,
                    as_of: str | None = None,
                    attribution: str = "historical") -> dict[str, Any]:
        if attribution not in ("historical", "current"):
            raise ValueError("attribution must be 'historical' or 'current'")
        audit: dict[str, Any] | None = None
        if as_of is not None:
            acols, arows = await self._repository.get_table("MIS_AUDIT_SNAPSHOT_LOAD")
            audit = next((dict(zip(acols, row)) for row in arows
                          if str(row[acols.index("snapshot_date")])[:10] == as_of), None)
            if (audit is None or audit["status"] != "PASS"
                    or str(audit["source_max_date"])[:10] != as_of):
                raise ValueError(f"Snapshot {as_of} is unavailable or failed its quality gate")
            to = min(to or as_of[:7], as_of[:7])
            if from_ is not None and from_ > to:
                from_ = to
        scope = user.code if user and not user.is_analyst else "full"
        cache_key = ("drill", self._snapshot_id(), scope, report_id, target, key, from_, to,
                     as_of, attribution)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        payload = await reporting.drill(report_id, self._repo_for(user, as_of, attribution),
                                        from_, to, target, key)
        if as_of is not None and audit is not None:
            data_through = str(audit["source_max_date"])[:10]
            payload["reporting_context"] = {
                "as_of": as_of, "data_through": data_through,
                "load_id": audit["load_id"], "loaded_at": audit["loaded_at"],
                "attribution": attribution,
                "complete": audit["status"] == "PASS" and data_through == as_of,
            }
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
