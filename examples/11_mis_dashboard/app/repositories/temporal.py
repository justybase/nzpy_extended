"""Repository decorator applying an exact reporting cut-off and attribution."""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Any

from app.repositories.base import MISRepository


class AsOfMISRepository(MISRepository):
    """Filter facts to ``as_of`` and optionally restate sales to current org.

    The wrapper is intentionally composable with ``ScopedMISRepository``:
    temporal attribution happens first and authorization is evaluated against
    the resulting reporting branch/region afterwards.
    """

    def __init__(self, inner: MISRepository, as_of: str,
                 attribution: str = "historical") -> None:
        try:
            dt.date.fromisoformat(as_of)
        except (TypeError, ValueError):
            raise ValueError(f"as_of must be YYYY-MM-DD, got: {as_of}") from None
        if attribution not in ("historical", "current"):
            raise ValueError("attribution must be 'historical' or 'current'")
        self._inner = inner
        self._as_of = as_of
        self._month = as_of[:7]
        self._attribution = attribution
        self._assignments: dict[int, tuple[int, int]] | None = None

    async def _current_assignments(self) -> dict[int, tuple[int, int]]:
        if self._assignments is not None:
            return self._assignments
        cols, rows = await self._inner.get_table("MIS_DIM_ORG_ASSIGNMENT")
        result = {}
        for row in rows:
            record = dict(zip(cols, row))
            if str(record["valid_from"])[:10] <= self._as_of <= str(record["valid_to"])[:10]:
                result[record["advisor_id"]] = (record["branch_id"], record["region_id"])
        self._assignments = result
        return result

    async def get_table(self, name: str) -> tuple[list[str], list[list[Any]]]:
        cols, rows = await self._inner.get_table(name)
        if name == "MIS_FACT_SALES":
            date_idx = cols.index("sale_date")
            result = [list(row) for row in rows if str(row[date_idx])[:10] <= self._as_of]
            if self._attribution == "current":
                branch_idx, advisor_idx = cols.index("branch_id"), cols.index("advisor_id")
                assignments = await self._current_assignments()
                for row in result:
                    assignment = assignments.get(row[advisor_idx])
                    if assignment:
                        row[branch_idx] = assignment[0]
            return cols, result
        if name == "MIS_DIM_ADVISOR" and self._attribution == "current":
            result = [list(row) for row in rows]
            branch_idx, advisor_idx = cols.index("branch_id"), cols.index("advisor_id")
            assignments = await self._current_assignments()
            for row in result:
                assignment = assignments.get(row[advisor_idx])
                if assignment:
                    row[branch_idx] = assignment[0]
            return cols, result
        date_columns = {
            "MIS_FACT_BRANCH_PLAN": "plan_month",
            "MIS_FACT_ADVISOR_PERF": "perf_month",
            "MIS_FACT_CUSTOMER_MOVEMENT": "movement_month",
        }
        if name in date_columns:
            idx = cols.index(date_columns[name])
            year, month, day = map(int, self._as_of.split("-"))
            closed = day == calendar.monthrange(year, month)[1]
            # Monthly movement is a closed-period fact.  Do not expose the
            # current partial month as if its full acquisition/churn result
            # were already known.  Plans and advisor quality/performance can
            # be shown for the current month because they are used for pacing.
            if name == "MIS_FACT_CUSTOMER_MOVEMENT" and not closed:
                return cols, [row for row in rows if str(row[idx])[:7] < self._month]
            return cols, [row for row in rows if str(row[idx])[:7] <= self._month]
        if name == "MIS_FACT_CAMPAIGN_RESULTS":
            # Campaign results are published at campaign end.  A campaign
            # ending later in the selected month is future information and
            # must not leak into an earlier point-in-time report.
            campaign_cols, campaign_rows = await self._inner.get_table("MIS_DIM_CAMPAIGN")
            campaign_id_idx = cols.index("campaign_id")
            dim_id_idx = campaign_cols.index("campaign_id")
            end_idx = campaign_cols.index("end_date")
            start_idx = campaign_cols.index("start_date")
            available_ids = {
                row[dim_id_idx] for row in campaign_rows
                if ((row[end_idx] is not None and str(row[end_idx])[:10] <= self._as_of)
                    or (row[end_idx] is None and row[start_idx] is not None
                        and str(row[start_idx])[:10] <= self._as_of))
            }
            return cols, [row for row in rows if row[campaign_id_idx] in available_ids]
        if name == "MIS_FACT_BALANCES":
            idx = cols.index("balance_month")
            year, month, day = map(int, self._as_of.split("-"))
            closed = day == calendar.monthrange(year, month)[1]
            return cols, [row for row in rows
                          if str(row[idx])[:7] < self._month
                          or (closed and str(row[idx])[:7] == self._month)]
        return cols, rows

    async def refresh_all(self) -> dict[str, Any]:
        return await self._inner.refresh_all()

    async def get_table_slice(self, name: str, column: str, value: Any):
        return await self._inner.get_table_slice(name, column, value)

    def snapshot(self) -> dict[str, Any]:
        return self._inner.snapshot()

    def is_ready(self) -> bool:
        return self._inner.is_ready()

    @property
    def last_error(self) -> str | None:
        return self._inner.last_error
