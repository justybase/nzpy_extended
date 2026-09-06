"""
ScopedMISRepository — row-level role masking for the MIS report pages.

Wraps any MISRepository and returns only the fact rows the signed-in user may
see (dimension tables pass through unchanged). Because every report builder
reads through the repository, one wrapper masks the whole app: overview,
sales reports, balances, client movement, campaign results, plans, the sales
ledger and drill-downs.

    ANALYST         - no filtering
    AREA_MANAGER    - branches of one region (and their advisors)
    BRANCH_MANAGER  - one branch (and its advisors)
    ADVISOR         - their own sales / ratings; their branch's other facts
"""

from __future__ import annotations

from typing import Any

from app.core.roles import SessionUser
from app.repositories.base import MISRepository

# fact tables and the entity column they are scoped by
BRANCH_FACTS = ("MIS_FACT_BALANCES", "MIS_FACT_CUSTOMER_MOVEMENT",
                "MIS_FACT_CAMPAIGN_RESULTS", "MIS_FACT_BRANCH_PLAN")


class ScopedMISRepository(MISRepository):
    def __init__(self, inner: MISRepository, user: SessionUser | None) -> None:
        self._inner = inner
        self._user = user
        self._branch_ids: set[int] | None = None
        self._advisor_ids: set[int] | None = None

    # -- scope resolution ----------------------------------------------------

    async def _scope(self) -> tuple[set[int] | None, set[int] | None]:
        """(allowed branch ids, allowed advisor ids); None = everything."""
        user = self._user
        if self._branch_ids is not None:
            return self._branch_ids, self._advisor_ids
        if user is None or user.has_full_scope:
            self._branch_ids, self._advisor_ids = None, None
            return None, None

        bcols, brows = await self._inner.get_table("MIS_DIM_BRANCH")
        if user.is_region_scoped:
            bi, ri = bcols.index("branch_id"), bcols.index("region_id")
            branch_ids = {r[bi] for r in brows if r[ri] == user.region_id}
        else:
            bi = bcols.index("branch_id")
            branch_ids = {user.branch_id} if user.branch_id else set()

        if user.is_advisor_scoped:
            advisor_ids = {user.advisor_id} if user.advisor_id else set()
        else:
            acols, arows = await self._inner.get_table("MIS_DIM_ADVISOR")
            ai, ab = acols.index("advisor_id"), acols.index("branch_id")
            advisor_ids = {r[ai] for r in arows if r[ab] in branch_ids}

        self._branch_ids, self._advisor_ids = branch_ids, advisor_ids
        return branch_ids, advisor_ids

    # -- MISRepository interface ---------------------------------------------

    async def get_table(self, name: str):
        cols, rows = await self._inner.get_table(name)
        branch_ids, advisor_ids = await self._scope()
        if name == "MIS_FACT_SALES":
            if advisor_ids is not None and self._user is not None \
                    and self._user.is_advisor_scoped:
                ai = cols.index("advisor_id")
                return cols, [r for r in rows if r[ai] in advisor_ids]
            if branch_ids is not None:
                bi = cols.index("branch_id")
                return cols, [r for r in rows if r[bi] in branch_ids]
        elif name == "MIS_FACT_ADVISOR_PERF":
            if advisor_ids is not None:
                ai = cols.index("advisor_id")
                return cols, [r for r in rows if r[ai] in advisor_ids]
        elif name in BRANCH_FACTS and branch_ids is not None:
            bi = cols.index("branch_id")
            return cols, [r for r in rows if r[bi] in branch_ids]
        return cols, rows

    async def refresh_all(self) -> dict[str, Any]:
        return await self._inner.refresh_all()

    async def get_table_slice(self, name: str, column: str, value: Any):
        # Slice first, then reuse the same row-level masking rules.
        cols, rows = await self._inner.get_table_slice(name, column, value)
        if name == "MIS_FACT_PERFORMANCE_SNAPSHOT":
            branch_ids, advisor_ids = await self._scope()
            if advisor_ids is not None and self._user is not None and self._user.is_advisor_scoped:
                ai = cols.index("advisor_id")
                return cols, [row for row in rows if row[ai] in advisor_ids]
            if branch_ids is not None:
                bi = cols.index("historical_branch_id")
                return cols, [row for row in rows if row[bi] in branch_ids]
        return cols, rows

    def snapshot(self) -> dict[str, Any]:
        return self._inner.snapshot()

    def is_ready(self) -> bool:
        return self._inner.is_ready()

    @property
    def last_error(self) -> str | None:
        return self._inner.last_error
