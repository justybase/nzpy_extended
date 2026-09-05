"""
PeopleService — advisor performance panels and the personal "my branch" /
"my results" cumulative views.

Everything is computed from the cached MIS_* tables; computed payloads are
cached in a TTLCache keyed by (scope, code, month), so repeated requests never
re-aggregate the sales detail and never touch Netezza.

    GET /api/people/advisors                  -> picker list
    GET /api/people/advisors/{code}           -> vertical info + ratings panel
    GET /api/people/cumulative?scope=&code=&month=
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from cachetools import TTLCache

from app.repositories.base import MISRepository

MONTH_LABELS = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"]


def _month_label(ym: str) -> str:
    y, m = ym.split("-")
    return f"{MONTH_LABELS[int(m) - 1]} {y}"


def _days_in_month(ym: str) -> int:
    y, m = int(ym[:4]), int(ym[5:7])
    nxt = dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1)
    return (nxt - dt.date(y, m, 1)).days


class UnknownEntity(Exception):
    pass


class PeopleService:
    def __init__(self, repository: MISRepository, ttl: int = 120,
                 maxsize: int = 256) -> None:
        self._repository = repository
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)

    # -- picker list ---------------------------------------------------------

    async def advisor_list(self) -> list[dict[str, Any]]:
        acols, arows = await self._repository.get_table("MIS_DIM_ADVISOR")
        bcols, brows = await self._repository.get_table("MIS_DIM_BRANCH")
        rcols, rrows = await self._repository.get_table("MIS_DIM_REGION")
        branch_by_id = {r[bcols.index("branch_id")]: r for r in brows}
        region_by_id = {r[rcols.index("region_id")]: r for r in rrows}
        out: list[dict[str, Any]] = []
        for r in arows:
            b = branch_by_id.get(r[acols.index("branch_id")])
            reg = region_by_id.get(b[bcols.index("region_id")]) if b else None
            out.append({
                "code": r[acols.index("advisor_code")],
                "first_name": r[acols.index("first_name")],
                "last_name": r[acols.index("last_name")],
                "name": f"{r[acols.index('first_name')]} {r[acols.index('last_name')]}",
                "role": r[acols.index("role")],
                "status": r[acols.index("status")],
                "branch_code": b[bcols.index("branch_code")] if b else "",
                "branch_name": b[bcols.index("branch_name")] if b else "",
                "city": b[bcols.index("city")] if b else "",
                "region_name": reg[rcols.index("region_name")] if reg else "",
            })
        out.sort(key=lambda a: (a["branch_code"], a["last_name"]))
        return out

    async def branch_list(self) -> list[dict[str, Any]]:
        bcols, brows = await self._repository.get_table("MIS_DIM_BRANCH")
        rcols, rrows = await self._repository.get_table("MIS_DIM_REGION")
        region_by_id = {r[rcols.index("region_id")]: r for r in rrows}
        out: list[dict[str, Any]] = []
        for r in brows:
            reg = region_by_id.get(r[bcols.index("region_id")])
            out.append({
                "code": r[bcols.index("branch_code")],
                "name": r[bcols.index("branch_name")],
                "city": r[bcols.index("city")],
                "region_name": reg[rcols.index("region_name")] if reg else "",
            })
        return out

    # -- advisor panel -------------------------------------------------------

    async def advisor_panel(self, code: str) -> dict[str, Any]:
        key = ("panel", code)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        payload = await self._build_panel(code)
        self._cache[key] = payload
        return payload

    async def _build_panel(self, code: str) -> dict[str, Any]:
        repo = self._repository
        acols, arows = await repo.get_table("MIS_DIM_ADVISOR")
        bcols, brows = await repo.get_table("MIS_DIM_BRANCH")
        rcols, rrows = await repo.get_table("MIS_DIM_REGION")
        pcols, prows = await repo.get_table("MIS_FACT_ADVISOR_PERF")
        scols, srows = await repo.get_table("MIS_FACT_SALES")

        advisor = next((r for r in arows
                        if r[acols.index("advisor_code")] == code), None)
        if advisor is None:
            raise UnknownEntity(f"Unknown advisor: {code}")
        advisor_id = advisor[acols.index("advisor_id")]
        branch = next((r for r in brows
                       if r[bcols.index("branch_id")]
                       == advisor[acols.index("branch_id")]), None)
        region = None
        if branch:
            region = next((r for r in rrows
                           if r[rcols.index("region_id")]
                           == branch[bcols.index("region_id")]), None)

        # booked sales per month for this advisor
        si, ai, di, ami = (scols.index("sale_status"), scols.index("advisor_id"),
                           scols.index("sale_date"), scols.index("amount"))
        actual: dict[str, float] = {}
        for r in srows:
            if r[si] == "BOOKED" and r[ai] == advisor_id:
                m = r[di][:7]
                actual[m] = actual.get(m, 0.0) + r[ami]

        perf: dict[str, dict[str, Any]] = {}
        for r in prows:
            if r[pcols.index("advisor_id")] != advisor_id:
                continue
            m = r[pcols.index("perf_month")][:7]
            perf[m] = {
                "plan": r[pcols.index("plan_amount")],
                "sales_score": r[pcols.index("sales_score")],
                "conversion_score": r[pcols.index("conversion_score")],
                "quality_score": r[pcols.index("quality_score")],
                "activity_score": r[pcols.index("activity_score")],
                "rating": r[pcols.index("rating")],
                "note": r[pcols.index("note")],
            }

        months = sorted(perf)
        monthly: list[list[Any]] = []
        for m in months:
            p = perf[m]
            achieved = round(actual.get(m, 0.0), 2)
            attainment = (round(achieved / p["plan"] * 100, 2)
                          if p["plan"] else None)
            monthly.append([m, _month_label(m), p["plan"], achieved,
                            attainment, p["sales_score"],
                            p["conversion_score"], p["quality_score"],
                            p["activity_score"], p["rating"], p["note"]])

        def attainment_for(m: str) -> float | None:
            p = perf[m]
            return round(actual.get(m, 0.0) / p["plan"] * 100, 2) if p["plan"] else None

        hire = advisor[acols.index("hire_date")]
        tenure = None
        if hire:
            h = dt.date.fromisoformat(str(hire))
            tenure = round((dt.date.today() - h).days / 365.25, 1)

        latest = monthly[-1] if monthly else None
        ratings = [r[9] for r in monthly]
        attainments = [r[4] for r in monthly if r[4] is not None]

        return {
            "code": code,
            "info": {
                "first_name": advisor[acols.index("first_name")],
                "last_name": advisor[acols.index("last_name")],
                "name": (f"{advisor[acols.index('first_name')]} "
                         f"{advisor[acols.index('last_name')]}"),
                "role": advisor[acols.index("role")],
                "status": advisor[acols.index("status")],
                "hire_date": str(hire) if hire else None,
                "tenure_years": tenure,
                "branch_code": branch[bcols.index("branch_code")] if branch else "",
                "branch_name": branch[bcols.index("branch_name")] if branch else "",
                "city": branch[bcols.index("city")] if branch else "",
                "region_name": region[rcols.index("region_name")] if region else "",
            },
            "summary": {
                "latest_month": months[-1] if months else None,
                "latest_attainment": latest[4] if latest else None,
                "latest_rating": latest[9] if latest else None,
                "latest_note": latest[10] if latest else None,
                "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
                "avg_attainment": (round(sum(a for a in attainments if a is not None)
                                         / len(attainments), 2) if attainments else None),
                "best_month": max(monthly, key=lambda r: (r[4] or 0))[0]
                              if monthly else None,
            },
            "columns": [
                {"key": "ym", "label": "Month", "fmt": "str"},
                {"key": "month", "label": "Month", "fmt": "str"},
                {"key": "plan", "label": "Plan", "fmt": "eur"},
                {"key": "achieved", "label": "Achieved", "fmt": "eur"},
                {"key": "attainment", "label": "Attainment %", "fmt": "pct"},
                {"key": "sales_score", "label": "Sales score", "fmt": "int"},
                {"key": "conversion_score", "label": "Conversion", "fmt": "int"},
                {"key": "quality_score", "label": "Quality", "fmt": "int"},
                {"key": "activity_score", "label": "Activity", "fmt": "int"},
                {"key": "rating", "label": "Rating", "fmt": "int"},
                {"key": "note", "label": "Note", "fmt": "str"},
            ],
            "rows": monthly,
            "charts": [
                {
                    "id": "attainment",
                    "type": "line",
                    "title": "Plan attainment by month",
                    "labels": [_month_label(m) for m in months],
                    "series": [
                        {"name": "Attainment %", "data": [attainment_for(m) for m in months]},
                    ],
                },
                {
                    "id": "scores",
                    "type": "line",
                    "title": "KPI scores by month",
                    "labels": [_month_label(m) for m in months],
                    "series": [
                        {"name": "Sales", "data": [perf[m]["sales_score"] for m in months]},
                        {"name": "Conversion", "data": [perf[m]["conversion_score"] for m in months]},
                        {"name": "Quality", "data": [perf[m]["quality_score"] for m in months]},
                        {"name": "Activity", "data": [perf[m]["activity_score"] for m in months]},
                    ],
                },
            ],
        }

    # -- cumulative (my branch / my results) ---------------------------------

    async def cumulative(self, scope: str, code: str, month: str) -> dict[str, Any]:
        key = ("cumulative", scope, code, month)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        payload = await self._build_cumulative(scope, code, month)
        self._cache[key] = payload
        return payload

    async def _build_cumulative(self, scope: str, code: str,
                                month: str) -> dict[str, Any]:
        repo = self._repository
        if scope not in ("branch", "advisor"):
            raise ValueError("scope must be 'branch' or 'advisor'")
        try:
            dt.date.fromisoformat(month + "-01")
        except ValueError:
            raise ValueError(f"month must be YYYY-MM, got: {month}") from None

        # entity info
        if scope == "branch":
            bcols, brows = await repo.get_table("MIS_DIM_BRANCH")
            rcols, rrows = await repo.get_table("MIS_DIM_REGION")
            entity = next((r for r in brows
                           if r[bcols.index("branch_code")] == code), None)
            if entity is None:
                raise UnknownEntity(f"Unknown branch: {code}")
            entity_id = entity[bcols.index("branch_id")]
            region = next((r for r in rrows
                           if r[rcols.index("region_id")]
                           == entity[bcols.index("region_id")]), None)
            info = {
                "code": code,
                "name": entity[bcols.index("branch_name")],
                "city": entity[bcols.index("city")],
                "region_name": region[rcols.index("region_name")] if region else "",
            }
        else:
            acols, arows = await repo.get_table("MIS_DIM_ADVISOR")
            bcols, brows = await repo.get_table("MIS_DIM_BRANCH")
            entity = next((r for r in arows
                           if r[acols.index("advisor_code")] == code), None)
            if entity is None:
                raise UnknownEntity(f"Unknown advisor: {code}")
            entity_id = entity[acols.index("advisor_id")]
            branch = next((r for r in brows
                           if r[bcols.index("branch_id")]
                           == entity[acols.index("branch_id")]), None)
            info = {
                "code": code,
                "name": (f"{entity[acols.index('first_name')]} "
                         f"{entity[acols.index('last_name')]}"),
                "role": entity[acols.index("role")],
                "branch_name": branch[bcols.index("branch_name")] if branch else "",
                "branch_code": branch[bcols.index("branch_code")] if branch else "",
            }

        # monthly plan for the entity
        plan = 0.0
        if scope == "branch":
            pcols, prows = await repo.get_table("MIS_FACT_BRANCH_PLAN")
            pi, pm, pa = (pcols.index("branch_id"), pcols.index("plan_month"),
                          pcols.index("plan_amount"))
            plan = next((r[pa] for r in prows
                         if r[pi] == entity_id and r[pm][:7] == month), 0.0)
        else:
            pcols, prows = await repo.get_table("MIS_FACT_ADVISOR_PERF")
            pi, pm, pa = (pcols.index("advisor_id"), pcols.index("perf_month"),
                          pcols.index("plan_amount"))
            plan = next((r[pa] for r in prows
                         if r[pi] == entity_id and r[pm][:7] == month), 0.0)

        # daily booked sales for this month and the previous one
        scols, srows = await repo.get_table("MIS_FACT_SALES")
        si, ei, di, ami = (scols.index("sale_status"), scols.index("branch_id")
                           if scope == "branch" else scols.index("advisor_id"),
                           scols.index("sale_date"), scols.index("amount"))
        prev_month = _prev_month(month)

        def daily_totals(ym: str) -> dict[int, float]:
            out: dict[int, float] = {}
            for r in srows:
                if r[si] != "BOOKED" or not r[di].startswith(ym):
                    continue
                if r[ei] != entity_id:
                    continue
                day = int(r[di][8:10])
                out[day] = out.get(day, 0.0) + r[ami]
            return out

        cur = daily_totals(month)
        prev = daily_totals(prev_month)
        days = _days_in_month(month)
        prev_days = _days_in_month(prev_month)

        rows: list[list[Any]] = []
        run = 0.0
        prev_run = 0.0
        for day in range(1, days + 1):
            daily = round(cur.get(day, 0.0), 2)
            run = round(run + daily, 2)
            prev_run = round(prev_run + prev.get(day, 0.0), 2)
            plan_cum = round(plan * day / days, 2)
            attainment = round(run / plan_cum * 100, 2) if plan_cum else None
            vs_prev = round((run - prev_run) / prev_run * 100, 2) if prev_run else None
            rows.append([f"{month}-{day:02d}", day, daily, run, plan_cum,
                         attainment, prev_run, vs_prev])

        last = rows[-1]
        attainment_total = (round(last[3] / plan * 100, 2) if plan else None)
        vs_prev_total = (round((last[3] - prev_run) / prev_run * 100, 2)
                         if prev_run else None)

        return {
            "scope": scope,
            "month": month,
            "info": info,
            "kpis": [
                {"key": "mtd", "label": "Month to date", "value": last[3], "fmt": "eur"},
                {"key": "plan", "label": "Monthly plan", "value": plan, "fmt": "eur"},
                {"key": "attainment", "label": "Plan attainment",
                 "value": attainment_total, "fmt": "pct"},
                {"key": "vs_prev", "label": "vs previous month",
                 "value": vs_prev_total, "fmt": "pct"},
                {"key": "days", "label": "Days elapsed", "value": days, "fmt": "int"},
            ],
            "columns": [
                {"key": "date", "label": "Date", "fmt": "str"},
                {"key": "day", "label": "Day", "fmt": "int"},
                {"key": "daily", "label": "Daily sales", "fmt": "eur"},
                {"key": "cumulative", "label": "Cumulative", "fmt": "eur"},
                {"key": "plan_cum", "label": "Plan (cumulative)", "fmt": "eur"},
                {"key": "attainment", "label": "Attainment %", "fmt": "pct"},
                {"key": "prev_cum", "label": "Prev month cumulative", "fmt": "eur"},
                {"key": "vs_prev", "label": "vs prev month %", "fmt": "pct"},
            ],
            "rows": rows,
            "charts": [
                {
                    "id": "cumulative",
                    "type": "line",
                    "title": "Cumulative sales within the month",
                    "labels": [f"{month}-{day:02d}" for day in range(1, days + 1)],
                    "series": [
                        {"name": "Actual", "data": [r[3] for r in rows]},
                        {"name": "Plan", "data": [r[4] for r in rows]},
                        {"name": "Previous month", "data": [r[6] for r in rows]},
                    ],
                },
            ],
        }


def _prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    if m == 1:
        return f"{y - 1}-12"
    return f"{y}-{m - 1:02d}"