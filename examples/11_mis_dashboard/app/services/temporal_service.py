"""Point-in-time MIS calculations over the prepared Netezza snapshot mart.

The service deliberately keeps business definitions in one place.  Routes do
HTTP validation, Netezza/fixtures supply rows, and this module owns period
semantics, temporal hierarchy attribution, role scope and league guardrails.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict
from typing import Any

from cachetools import TTLCache

from app.core.roles import SessionUser
from app.repositories.base import MISRepository


DEFINITIONS = {
    "DTD": "Selected day's activity versus the immediately preceding snapshot day",
    "MTD": "First calendar day of the month through the selected snapshot",
    "PMTD": "Same day range in the immediately preceding month",
    "MoM": "Last closed calendar month versus the preceding closed month",
    "YTD": "1 January through the snapshot versus the matching prior-year range",
    "YoY": "Current MTD versus the matching month/day one year earlier",
}


class SnapshotNotFound(LookupError):
    pass


def _date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"as_of must be YYYY-MM-DD, got: {value}") from None


def _previous_month_day(value: dt.date) -> dt.date:
    previous_end = value.replace(day=1) - dt.timedelta(days=1)
    return previous_end.replace(day=min(value.day, previous_end.day))


def _previous_year_day(value: dt.date) -> dt.date:
    return value.replace(year=value.year - 1, day=min(value.day, calendar.monthrange(value.year - 1, value.month)[1]))


def _pct_delta(current: float, reference: float) -> float | None:
    return round((current - reference) / reference * 100, 2) if reference else None


class TemporalMISService:
    def __init__(self, repository: MISRepository, ttl: int = 120,
                 maxsize: int = 256) -> None:
        self._repository = repository
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)

    def clear_cache(self) -> None:
        self._cache.clear()

    def _snapshot_token(self) -> tuple[tuple[str, str], ...]:
        tables = self._repository.snapshot().get("tables", {})
        return tuple(sorted((name, str(info.get("loaded_at", "")))
                            for name, info in tables.items()))

    async def available_snapshots(self) -> list[str]:
        cols, rows = await self._repository.get_table("MIS_AUDIT_SNAPSHOT_LOAD")
        idx = cols.index("snapshot_date")
        return sorted({str(r[idx])[:10] for r in rows})

    async def _audit(self, as_of: str) -> dict[str, Any]:
        cols, rows = await self._repository.get_table("MIS_AUDIT_SNAPSHOT_LOAD")
        record = next((r for r in rows if str(r[cols.index("snapshot_date")])[:10] == as_of), None)
        if record is None:
            dates = await self.available_snapshots()
            bounds = f"{dates[0]} through {dates[-1]}" if dates else "none"
            raise SnapshotNotFound(f"Snapshot {as_of} is unavailable; available range: {bounds}")
        return dict(zip(cols, record))

    async def context(self, as_of: str | None,
                      attribution: str = "historical",
                      allow_failed: bool = False) -> dict[str, Any]:
        if attribution not in ("historical", "current"):
            raise ValueError("attribution must be 'historical' or 'current'")
        dates = await self.available_snapshots()
        if not dates:
            raise SnapshotNotFound("No reporting snapshots are available")
        requested = as_of or dates[-1]
        _date(requested)
        audit = await self._audit(requested)
        data_through = str(audit["source_max_date"])[:10]
        if (audit["status"] != "PASS" or data_through != requested) and not allow_failed:
            raise SnapshotNotFound(
                f"Snapshot {requested} did not pass the reporting quality gate")
        return {
            "requested_as_of": requested,
            "as_of": requested,
            "data_through": data_through,
            "load_id": audit["load_id"],
            "loaded_at": audit["loaded_at"],
            "attribution": attribution,
            "complete": audit["status"] == "PASS" and data_through == requested,
            "stale": bool(self._repository.last_error),
            "definitions": DEFINITIONS,
        }

    async def _maps(self) -> dict[str, Any]:
        async def records(name: str) -> tuple[list[str], list[list[Any]]]:
            return await self._repository.get_table(name)
        b, r, a, o, p, d = await records("MIS_DIM_BRANCH"), await records("MIS_DIM_REGION"), \
            await records("MIS_DIM_ADVISOR"), await records("MIS_DIM_ORG_ASSIGNMENT"), \
            await records("MIS_FACT_ADVISOR_PERF"), await records("MIS_DIM_DATE")
        return {
            "branches": {row[b[0].index("branch_id")]: dict(zip(b[0], row)) for row in b[1]},
            "regions": {row[r[0].index("region_id")]: dict(zip(r[0], row)) for row in r[1]},
            "advisors": {row[a[0].index("advisor_id")]: dict(zip(a[0], row)) for row in a[1]},
            "assignments": [dict(zip(o[0], row)) for row in o[1]],
            "performance": [dict(zip(p[0], row)) for row in p[1]],
            "dates": {str(row[d[0].index("calendar_date")])[:10]: dict(zip(d[0], row)) for row in d[1]},
        }

    @staticmethod
    def _assignment(maps: dict[str, Any], advisor_id: int,
                    on_date: str) -> dict[str, Any] | None:
        return next((row for row in maps["assignments"]
                     if row["advisor_id"] == advisor_id
                     and str(row["valid_from"])[:10] <= on_date <= str(row["valid_to"])[:10]), None)

    @staticmethod
    def _allowed(user: SessionUser | None, advisor_id: int,
                 branch_id: int, region_id: int) -> bool:
        if user is None or user.is_analyst:
            return True
        if user.role == "AREA_MANAGER":
            return user.region_id == region_id
        if user.role == "BRANCH_MANAGER":
            return user.branch_id == branch_id
        return user.advisor_id == advisor_id

    async def _rows(self, snapshot: str, attribution: str,
                    attribution_date: str, user: SessionUser | None,
                    maps: dict[str, Any]) -> list[dict[str, Any]]:
        cols, raw = await self._repository.get_table_slice(
            "MIS_FACT_PERFORMANCE_SNAPSHOT", "snapshot_date", snapshot)
        rows: list[dict[str, Any]] = []
        for values in raw:
            row = dict(zip(cols, values))
            if attribution == "current":
                assignment = self._assignment(maps, row["advisor_id"], attribution_date)
                if assignment is None:
                    continue
                row["reporting_branch_id"] = assignment["branch_id"]
                row["reporting_region_id"] = assignment["region_id"]
            else:
                row["reporting_branch_id"] = row["historical_branch_id"]
                row["reporting_region_id"] = row["historical_region_id"]
            if self._allowed(user, row["advisor_id"], row["reporting_branch_id"],
                             row["reporting_region_id"]):
                rows.append(row)
        return rows

    async def _total(self, snapshot: str, measure: str, attribution: str,
                     attribution_date: str, user: SessionUser | None,
                     maps: dict[str, Any]) -> float:
        dates = maps["dates"]
        if snapshot not in dates:
            return 0.0
        rows = await self._rows(snapshot, attribution, attribution_date, user, maps)
        return round(sum(float(r.get(measure) or 0) for r in rows), 2)

    async def _ytd(self, end: dt.date, attribution: str, attribution_date: str,
                   user: SessionUser | None, maps: dict[str, Any]) -> float:
        total = 0.0
        month = dt.date(end.year, 1, 1)
        while month <= end:
            month_end = dt.date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
            snap = min(month_end, end)
            total += await self._total(snap.isoformat(), "sales_amount_mtd", attribution,
                                       attribution_date, user, maps)
            month = (month_end + dt.timedelta(days=1)).replace(day=1)
        return round(total, 2)

    async def performance(self, as_of: str | None, attribution: str,
                          dim: str, user: SessionUser | None = None) -> dict[str, Any]:
        if dim not in ("region", "branch", "advisor"):
            raise ValueError("dim must be region, branch or advisor")
        context = await self.context(as_of, attribution)
        selected = context["as_of"]
        key = ("performance", self._snapshot_token(), selected, attribution, dim,
               user.code if user else "full")
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        maps = await self._maps()
        point = _date(selected)
        rows = await self._rows(selected, attribution, selected, user, maps)
        mtd = round(sum(r["sales_amount_mtd"] or 0 for r in rows), 2)
        units = sum(r["sales_count_mtd"] or 0 for r in rows)
        commission = round(sum(r["commission_mtd"] or 0 for r in rows), 2)
        cancellations = sum(r["cancellations_mtd"] or 0 for r in rows)

        month = selected[:7]
        plans = {r["advisor_id"]: float(r["plan_amount"] or 0)
                 for r in maps["performance"] if str(r["perf_month"])[:7] == month
                 }
        # Plans are a denominator for the whole authorized population, not
        # only for advisors who happened to book a sale in this snapshot.
        # Keeping zero-activity entities in the analytic result prevents an
        # apparently strong MTD attainment caused by silently dropping their
        # targets.
        eligible_advisors: dict[int, dict[str, Any]] = {}
        for advisor_id in plans:
            advisor = maps["advisors"].get(advisor_id, {})
            hire_date = str(advisor.get("hire_date") or "")[:10]
            if hire_date and hire_date > selected:
                continue
            assignment = self._assignment(maps, advisor_id, selected)
            if assignment and self._allowed(user, advisor_id,
                                           assignment["branch_id"],
                                           assignment["region_id"]):
                eligible_advisors[advisor_id] = assignment
        # The KPI denominator must follow the same authorization scope as the
        # actuals.  Summing every network target here would make a branch or
        # area manager's attainment look artificially low and would also make
        # the pacing chart disagree with the analytic table below.
        full_plan = round(sum(plans[advisor_id]
                              for advisor_id in eligible_advisors), 2)
        calendar_row = maps["dates"][selected]
        elapsed_bd = calendar_row["business_day_of_month"]
        total_bd = calendar_row["business_days_in_month"]
        plan_mtd = round(full_plan * elapsed_bd / total_bd, 2) if total_bd else 0.0
        attainment = round(mtd / plan_mtd * 100, 2) if plan_mtd else None
        forecast = round(mtd / elapsed_bd * total_bd, 2) if elapsed_bd else None

        previous_day = point - dt.timedelta(days=1)
        pmtd_day = _previous_month_day(point)
        closed_end = point.replace(day=1) - dt.timedelta(days=1)
        prior_closed_end = closed_end.replace(day=1) - dt.timedelta(days=1)
        yoy_day = _previous_year_day(point)

        async def comparison(code: str, current_value: float, reference: float,
                             current_period: tuple[str, str], reference_period: tuple[str, str]) -> dict[str, Any]:
            return {
                "code": code, "label": DEFINITIONS[code], "current": current_value,
                "reference": reference, "delta": round(current_value - reference, 2),
                "delta_pct": _pct_delta(current_value, reference),
                "current_period": {"from": current_period[0], "to": current_period[1]},
                "reference_period": {"from": reference_period[0], "to": reference_period[1]},
            }

        today = await self._total(selected, "sales_amount_day", attribution, selected, user, maps)
        yesterday = await self._total(previous_day.isoformat(), "sales_amount_day", attribution, selected, user, maps)
        pmtd = await self._total(pmtd_day.isoformat(), "sales_amount_mtd", attribution, selected, user, maps)
        closed = await self._total(closed_end.isoformat(), "sales_amount_mtd", attribution, selected, user, maps)
        prior_closed = await self._total(prior_closed_end.isoformat(), "sales_amount_mtd", attribution, selected, user, maps)
        yoy = await self._total(yoy_day.isoformat(), "sales_amount_mtd", attribution, selected, user, maps)
        ytd = await self._ytd(point, attribution, selected, user, maps)
        prior_ytd = await self._ytd(yoy_day, attribution, selected, user, maps)
        comparisons = {
            "DTD": await comparison("DTD", today, yesterday, (selected, selected),
                                    (previous_day.isoformat(), previous_day.isoformat())),
            "MTD": await comparison("MTD", mtd, pmtd, (point.replace(day=1).isoformat(), selected),
                                    (pmtd_day.replace(day=1).isoformat(), pmtd_day.isoformat())),
            "PMTD": await comparison("PMTD", mtd, pmtd, (point.replace(day=1).isoformat(), selected),
                                     (pmtd_day.replace(day=1).isoformat(), pmtd_day.isoformat())),
            "MoM": await comparison("MoM", closed, prior_closed,
                                    (closed_end.replace(day=1).isoformat(), closed_end.isoformat()),
                                    (prior_closed_end.replace(day=1).isoformat(), prior_closed_end.isoformat())),
            "YTD": await comparison("YTD", ytd, prior_ytd,
                                    (dt.date(point.year, 1, 1).isoformat(), selected),
                                    (dt.date(yoy_day.year, 1, 1).isoformat(), yoy_day.isoformat())),
            "YoY": await comparison("YoY", mtd, yoy, (point.replace(day=1).isoformat(), selected),
                                    (yoy_day.replace(day=1).isoformat(), yoy_day.isoformat())),
        }

        grouped: dict[Any, dict[str, Any]] = {}
        for row in rows:
            entity_id = row["reporting_region_id"] if dim == "region" else \
                row["reporting_branch_id"] if dim == "branch" else row["advisor_id"]
            item = grouped.setdefault(entity_id, {"amount": 0.0, "units": 0, "advisors": set()})
            item["amount"] += row["sales_amount_mtd"] or 0
            item["units"] += row["sales_count_mtd"] or 0
            item["advisors"].add(row["advisor_id"])
        for advisor_id, assignment in eligible_advisors.items():
            entity_id = (assignment["region_id"] if dim == "region" else
                         assignment["branch_id"] if dim == "branch" else advisor_id)
            grouped.setdefault(entity_id, {"amount": 0.0, "units": 0,
                                           "advisors": set()})["advisors"].add(advisor_id)
        analytic = []
        for entity_id, values in grouped.items():
            if dim == "region":
                entity = maps["regions"].get(entity_id, {})
                code, name = entity.get("region_code"), entity.get("region_name")
            elif dim == "branch":
                entity = maps["branches"].get(entity_id, {})
                code, name = entity.get("branch_code"), entity.get("branch_name")
            else:
                entity = maps["advisors"].get(entity_id, {})
                code = entity.get("advisor_code")
                name = f"{entity.get('first_name', '')} {entity.get('last_name', '')}".strip()
            entity_plan = sum(plans.get(advisor_id, 0) for advisor_id in values["advisors"])
            entity_plan_mtd = entity_plan * elapsed_bd / total_bd if total_bd else 0
            analytic.append([code, name, values["units"], round(values["amount"], 2),
                             round(entity_plan_mtd, 2),
                             round(values["amount"] / entity_plan_mtd * 100, 2)
                             if entity_plan_mtd else None])
        analytic.sort(key=lambda row: row[3], reverse=True)

        trend_labels, trend_actual, trend_plan = [], [], []
        for day_number in range(1, point.day + 1):
            day = point.replace(day=day_number)
            iso = day.isoformat()
            value = await self._total(iso, "sales_amount_mtd", attribution, selected, user, maps)
            cal = maps["dates"][iso]
            paced = full_plan * cal["business_day_of_month"] / cal["business_days_in_month"]
            trend_labels.append(iso)
            trend_actual.append(value)
            trend_plan.append(round(paced, 2))

        payload = {
            "reporting_context": context,
            "kpis": [
                {"key": "mtd", "label": "MTD sales", "value": mtd, "fmt": "eur"},
                {"key": "plan_mtd", "label": "MTD plan", "value": plan_mtd, "fmt": "eur"},
                {"key": "attainment", "label": "Plan attainment", "value": attainment,
                 "fmt": "pct", "target": 100, "target_fmt": "pct",
                 "status": "good" if attainment is not None and attainment >= 100 else
                           "warning" if attainment is not None and attainment >= 80 else "critical"},
                {"key": "forecast", "label": "Month-end forecast", "value": forecast, "fmt": "eur"},
                {"key": "units", "label": "Products sold", "value": units, "fmt": "int"},
                {"key": "commission", "label": "Commission", "value": commission, "fmt": "eur"},
                {"key": "cancellations", "label": "Cancellations", "value": cancellations, "fmt": "int"},
            ],
            "comparisons": comparisons,
            "columns": [
                {"key": f"{dim}_code", "label": "Code"},
                {"key": f"{dim}_name", "label": dim.title()},
                {"key": "units", "label": "Sales", "fmt": "int"},
                {"key": "amount", "label": "MTD sales (EUR)", "fmt": "eur"},
                {"key": "plan_mtd", "label": "MTD plan (EUR)", "fmt": "eur"},
                {"key": "attainment", "label": "Attainment %", "fmt": "pct"},
            ],
            "rows": analytic,
            "charts": [{"id": "mtd_pacing", "type": "line", "title": "MTD actual vs business-day plan",
                        "labels": trend_labels,
                        "series": [{"name": "Actual", "data": trend_actual},
                                   {"name": "Plan", "data": trend_plan}]}],
        }
        self._cache[key] = payload
        return payload

    async def hierarchy(self, as_of: str | None, attribution: str,
                        user: SessionUser | None = None) -> dict[str, Any]:
        context = await self.context(as_of, attribution)
        maps = await self._maps()
        selected = context["as_of"]
        regions: dict[int, dict[str, Any]] = {}
        for advisor_id, advisor in maps["advisors"].items():
            hire_date = str(advisor.get("hire_date") or "")[:10]
            if hire_date and hire_date > selected:
                # A point-in-time hierarchy must not show people who had not
                # joined the network at the selected reporting date.
                continue
            assignment = self._assignment(maps, advisor_id, selected)
            if assignment is None or not self._allowed(user, advisor_id, assignment["branch_id"], assignment["region_id"]):
                continue
            region = maps["regions"][assignment["region_id"]]
            branch = maps["branches"][assignment["branch_id"]]
            region_node = regions.setdefault(assignment["region_id"], {
                "code": region["region_code"], "name": region["region_name"], "branches": {}})
            branch_node = region_node["branches"].setdefault(assignment["branch_id"], {
                "code": branch["branch_code"], "name": branch["branch_name"], "advisors": []})
            branch_node["advisors"].append({
                "code": advisor["advisor_code"],
                "name": f"{advisor['first_name']} {advisor['last_name']}",
                "role": advisor["role"], "valid_from": str(assignment["valid_from"])[:10],
            })
        region_rows = []
        for region in sorted(regions.values(), key=lambda value: value["name"]):
            region["branches"] = sorted(region["branches"].values(), key=lambda value: value["name"])
            region_rows.append(region)
        changes = []
        for assignment in maps["assignments"]:
            if str(assignment["valid_from"])[:10] <= str(dt.date(2024, 1, 1)) or str(assignment["valid_from"])[:10] > selected:
                continue
            advisor = maps["advisors"][assignment["advisor_id"]]
            branch = maps["branches"][assignment["branch_id"]]
            if not self._allowed(user, assignment["advisor_id"], assignment["branch_id"], assignment["region_id"]):
                continue
            changes.append({"effective_date": str(assignment["valid_from"])[:10],
                            "advisor_code": advisor["advisor_code"],
                            "advisor_name": f"{advisor['first_name']} {advisor['last_name']}",
                            "to_branch": branch["branch_name"],
                            "reason": assignment["change_reason"]})
        changes.sort(key=lambda row: row["effective_date"], reverse=True)
        return {"reporting_context": context, "regions": region_rows, "changes": changes}

    async def league(self, as_of: str | None, attribution: str, level: str,
                     user: SessionUser | None = None) -> dict[str, Any]:
        if level not in ("advisor", "branch"):
            raise ValueError("level must be advisor or branch")
        context = await self.context(as_of, attribution)
        selected = context["as_of"]
        maps = await self._maps()
        rows = await self._rows(selected, attribution, selected, user, maps)
        pmtd_date = _previous_month_day(_date(selected)).isoformat()
        previous = await self._rows(pmtd_date, attribution, selected, user, maps)
        previous_by_advisor: dict[int, float] = defaultdict(float)
        for row in previous:
            previous_by_advisor[row["advisor_id"]] += row["sales_amount_mtd"] or 0
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            entity_id = row["advisor_id"] if level == "advisor" else row["reporting_branch_id"]
            item = grouped.setdefault(entity_id, {"amount": 0.0, "units": 0, "plan": 0.0,
                                                   "quality": [], "activity": [], "advisors": set()})
            item["amount"] += row["sales_amount_mtd"] or 0
            item["units"] += row["sales_count_mtd"] or 0
            item["quality"].append(row["quality_score"] or 0)
            item["activity"].append(row["activity_score"] or 0)
            item["advisors"].add(row["advisor_id"])
        month_plans = {
            p["advisor_id"]: float(p["plan_amount"] or 0)
            for p in maps["performance"]
            if str(p["perf_month"])[:7] == selected[:7]
        }
        # Keep zero-activity advisors/branches visible as non-qualified. A
        # leaderboard that silently drops people with no sales cannot explain
        # the minimum-volume guardrail or provide a useful coaching view.
        for advisor_id in month_plans:
            advisor = maps["advisors"].get(advisor_id, {})
            hire_date = str(advisor.get("hire_date") or "")[:10]
            if hire_date and hire_date > selected:
                continue
            assignment = self._assignment(maps, advisor_id, selected)
            if assignment is None or not self._allowed(
                    user, advisor_id, assignment["branch_id"], assignment["region_id"]):
                continue
            entity_id = advisor_id if level == "advisor" else assignment["branch_id"]
            item = grouped.setdefault(entity_id, {"amount": 0.0, "units": 0,
                                                  "plan": 0.0, "quality": [],
                                                  "activity": [], "advisors": set()})
            item["advisors"].add(advisor_id)
            if level == "branch" or not item["quality"]:
                perf = next((p for p in maps["performance"]
                             if p["advisor_id"] == advisor_id
                             and str(p["perf_month"])[:7] == selected[:7]), None)
                if perf is not None:
                    item["quality"].append(perf["quality_score"] or 0)
                    item["activity"].append(perf["activity_score"] or 0)
        for item in grouped.values():
            unique = item["advisors"]
            item["plan"] = sum(next((float(p["plan_amount"] or 0) for p in maps["performance"]
                                     if p["advisor_id"] == aid and str(p["perf_month"])[:7] == selected[:7]), 0)
                               for aid in unique)
            item["previous"] = sum(previous_by_advisor[aid] for aid in unique)

        ranked = []
        cal = maps["dates"][selected]
        pacing = cal["business_day_of_month"] / cal["business_days_in_month"]
        for entity_id, value in grouped.items():
            plan_mtd = value["plan"] * pacing
            attainment = value["amount"] / plan_mtd * 100 if plan_mtd else 0
            growth = _pct_delta(value["amount"], value["previous"])
            quality = sum(value["quality"]) / len(value["quality"]) if value["quality"] else 0
            activity = sum(value["activity"]) / len(value["activity"]) if value["activity"] else 0
            eligible = value["units"] >= 10 and quality >= 80
            momentum = max(0, min(120, 100 + (growth or 0)))
            score = round(.50 * min(120, attainment) + .20 * momentum + .15 * quality + .15 * activity, 2)
            badges = []
            if attainment >= 100: badges.append("Target achiever")
            if quality >= 95: badges.append("Quality champion")
            if growth is not None and growth >= 10: badges.append("Momentum")
            if level == "advisor":
                entity = maps["advisors"][entity_id]
                code = entity["advisor_code"]
                name = f"{entity['first_name']} {entity['last_name']}"
            else:
                entity = maps["branches"][entity_id]
                code, name = entity["branch_code"], entity["branch_name"]
            ranked.append({"code": code, "name": name, "eligible": eligible, "score": score,
                           "attainment": round(attainment, 2), "growth": growth,
                           "quality": round(quality, 2), "activity": round(activity, 2),
                           "units": value["units"], "badges": ", ".join(badges) or "—"})
        ranked.sort(key=lambda row: (not row["eligible"], -row["score"], row["name"]))
        position = 0
        display = []
        current_user_position = None
        for item in ranked:
            if item["eligible"]:
                position += 1
                rank: int | None = position
            else:
                rank = None
            if user and level == "advisor" and user.advisor_id is not None \
                    and item["code"] == f"P{user.advisor_id:04d}":
                current_user_position = rank
            display.append([rank, item["code"], item["name"], item["eligible"], item["score"],
                            item["attainment"], item["growth"], item["quality"],
                            item["activity"], item["units"], item["badges"]])
        return {
            "reporting_context": context, "level": level,
            "rules": {"weights": {"attainment": 50, "PMTD growth": 20, "quality": 15, "activity": 15},
                      "minimum_sales": 10, "minimum_quality": 80,
                      "attainment_cap": 120, "public_view": "Top 10 only"},
            "columns": [
                {"key": "rank", "label": "Rank", "fmt": "int"}, {"key": "code", "label": "Code"},
                {"key": "name", "label": level.title()}, {"key": "eligible", "label": "Eligible"},
                {"key": "score", "label": "Score", "fmt": "dec"},
                {"key": "attainment", "label": "Attainment %", "fmt": "pct"},
                {"key": "growth", "label": "PMTD growth %", "fmt": "pct"},
                {"key": "quality", "label": "Quality", "fmt": "dec"},
                {"key": "activity", "label": "Activity", "fmt": "dec"},
                {"key": "units", "label": "Sales", "fmt": "int"},
                {"key": "badges", "label": "Badges"}],
            "rows": display[:10] if user and user.role == "ADVISOR" else display,
            "current_user_position": current_user_position,
        }

    async def quality(self, as_of: str | None, attribution: str) -> dict[str, Any]:
        context = await self.context(as_of, attribution, allow_failed=True)
        selected = context["as_of"]
        audit = await self._audit(selected)
        maps = await self._maps()
        overlaps = 0
        by_advisor: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in maps["assignments"]:
            by_advisor[row["advisor_id"]].append(row)
        for history in by_advisor.values():
            history.sort(key=lambda row: str(row["valid_from"]))
            for left, right in zip(history, history[1:]):
                if str(left["valid_to"])[:10] >= str(right["valid_from"])[:10]:
                    overlaps += 1
        branch_ids, advisor_ids = set(maps["branches"]), set(maps["advisors"])
        orphans = sum(1 for row in maps["assignments"]
                      if row["branch_id"] not in branch_ids or row["advisor_id"] not in advisor_ids)
        sales_cols, sales_rows = await self._repository.get_table("MIS_FACT_SALES")
        sale_id_idx = sales_cols.index("sale_id")
        duplicate_sale_ids = len(sales_rows) - len({row[sale_id_idx] for row in sales_rows})
        snapshots = await self.available_snapshots()
        audit_cols, audit_rows = await self._repository.get_table("MIS_AUDIT_SNAPSHOT_LOAD")
        audit_date_idx = audit_cols.index("snapshot_date")
        duplicate_snapshot_dates = len(audit_rows) - len({str(row[audit_date_idx])[:10]
                                                          for row in audit_rows})
        snapshot_cols, snapshot_rows = await self._repository.get_table_slice(
            "MIS_FACT_PERFORMANCE_SNAPSHOT", "snapshot_date", selected)
        snapshot_date_idx = snapshot_cols.index("snapshot_date")
        snapshot_advisor_idx = snapshot_cols.index("advisor_id")
        snapshot_branch_idx = snapshot_cols.index("historical_branch_id")
        duplicate_snapshot_rows = len(snapshot_rows) - len({
            (str(row[snapshot_date_idx])[:10], row[snapshot_advisor_idx],
             row[snapshot_branch_idx]) for row in snapshot_rows
        })
        snapshot_row_difference = len(snapshot_rows) - int(audit["snapshot_rows"] or 0)
        expected_days = (_date(snapshots[-1]) - _date(snapshots[0])).days + 1
        missing_days = expected_days - len(snapshots)
        difference = round(float(audit["difference_amount"] or 0), 2)
        monotonic_violations = 0
        previous_values: dict[tuple[int, int], tuple[int, float]] = {}
        point = _date(selected)
        for day_number in range(1, point.day + 1):
            iso = point.replace(day=day_number).isoformat()
            for row in await self._rows(iso, "historical", selected, None, maps):
                key = (row["advisor_id"], row["historical_branch_id"])
                value = (int(row["sales_count_mtd"] or 0), float(row["sales_amount_mtd"] or 0))
                old = previous_values.get(key)
                if old is not None and (value[0] < old[0] or value[1] < old[1]):
                    monotonic_violations += 1
                previous_values[key] = value
        checks = [
            {"id": "freshness", "label": "Data through selected date", "status": "PASS" if context["complete"] else "FAIL",
             "value": context["data_through"], "detail": "Source watermark equals the selected snapshot."},
            {"id": "reconciliation", "label": "Source-to-mart reconciliation", "status": "PASS" if difference == 0 else "FAIL",
             "value": difference, "detail": "Booked MTD amount difference (EUR)."},
            {"id": "scd_overlap", "label": "SCD2 ranges do not overlap", "status": "PASS" if overlaps == 0 else "FAIL",
             "value": overlaps, "detail": "Overlapping advisor assignment ranges."},
            {"id": "referential_integrity", "label": "Hierarchy keys resolve", "status": "PASS" if orphans == 0 else "FAIL",
             "value": orphans, "detail": "Assignments with missing advisor or branch."},
            {"id": "duplicate_sale_ids", "label": "Sale identifiers are unique",
             "status": "PASS" if duplicate_sale_ids == 0 else "FAIL",
             "value": duplicate_sale_ids, "detail": "Duplicate source sale identifiers."},
            {"id": "duplicate_snapshots", "label": "Snapshot dates are unique",
             "status": "PASS" if duplicate_snapshot_dates == 0 else "FAIL",
             "value": duplicate_snapshot_dates, "detail": "Duplicate dates in the audit ledger."},
            {"id": "snapshot_row_grain", "label": "Snapshot row grain is unique",
             "status": "PASS" if duplicate_snapshot_rows == 0 else "FAIL",
             "value": duplicate_snapshot_rows, "detail": "Duplicate advisor/branch rows at the selected date."},
            {"id": "snapshot_row_count", "label": "Audit row count matches mart",
             "status": "PASS" if snapshot_row_difference == 0 else "FAIL",
             "value": snapshot_row_difference, "detail": "Mart rows minus the audit ledger count."},
            {"id": "snapshot_continuity", "label": "Daily snapshot continuity", "status": "PASS" if missing_days == 0 else "FAIL",
             "value": missing_days, "detail": "Missing calendar dates in the audit ledger."},
            {"id": "mtd_monotonicity", "label": "MTD measures are monotonic", "status": "PASS" if monotonic_violations == 0 else "FAIL",
             "value": monotonic_violations, "detail": "Unexpected decreases within advisor/branch MTD series."},
        ]
        overall = "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL"
        return {"reporting_context": context, "overall_status": overall, "checks": checks}
