"""Exception-first reporting logic for the Sales Decision Cockpit.

The service deliberately returns decision-ready facts rather than a catalogue
of unrelated reports. Every recommendation is derived from the selected
weekly scorecard, its target, and the leading indicators in the same row set.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from cachetools import TTLCache
from nzpy_extended import Error

from app.repositories.base import SDCRepository

METRICS: dict[str, dict[str, str]] = {
    "sales_amount": {"label": "Sales amount", "actual": "sales_amount", "target": "target_amount", "fmt": "eur"},
    "sales_count": {"label": "Sales count", "actual": "sales_count", "target": "target_count", "fmt": "int"},
    "margin_amount": {"label": "Gross margin", "actual": "margin_amount", "target": "target_margin", "fmt": "eur"},
}

DIMENSIONS = {
    "product": "Product",
    "channel": "Channel",
    "region": "Region",
    "unit": "Sales unit",
}

REQUIRED_TABLES = (
    "SDC_DIM_DATE",
    "SDC_DIM_REGION",
    "SDC_DIM_UNIT",
    "SDC_DIM_SELLER",
    "SDC_DIM_PRODUCT",
    "SDC_DIM_CHANNEL",
    "SDC_FACT_WEEKLY_SCORECARD",
    "SDC_FACT_WEEKLY_TARGET",
    "SDC_AUDIT_DATASET_LOAD",
    "SDC_CONTROL_DATASET_LOAD",
)


class DataNotReady(RuntimeError):
    """Raised when the database has not been seeded yet."""


class CockpitService:
    def __init__(self, repository: SDCRepository, ttl: int = 120) -> None:
        self._repository = repository
        self._tables_cache: TTLCache[str, dict[str, list[dict[str, Any]]]] = TTLCache(
            maxsize=2, ttl=ttl
        )

    def clear_cache(self) -> None:
        self._tables_cache.clear()

    async def _tables(self) -> dict[str, list[dict[str, Any]]]:
        cached = self._tables_cache.get("tables")
        if cached is not None:
            return cached
        result: dict[str, list[dict[str, Any]]] = {}
        for name in REQUIRED_TABLES:
            try:
                columns, rows = await self._repository.get_table(name)
            except (KeyError, Error) as exc:
                raise DataNotReady(f"Missing table {name}; run: python seed.py") from exc
            result[name] = [dict(zip(columns, row)) for row in rows]
        self._tables_cache["tables"] = result
        return result

    @staticmethod
    def _as_float(value: Any) -> float:
        return float(value or 0)

    @staticmethod
    def _as_int(value: Any) -> int:
        return int(value or 0)

    @staticmethod
    def _date(value: Any) -> dt.date:
        return dt.date.fromisoformat(str(value)[:10])

    async def meta(self) -> dict[str, Any]:
        tables = await self._tables()
        weeks = sorted({str(row["week_start"])[:10] for row in tables["SDC_FACT_WEEKLY_TARGET"]})
        regions = [
            {"code": str(row["region_code"]), "label": str(row["region_name"])}
            for row in sorted(tables["SDC_DIM_REGION"], key=lambda item: item["region_code"])
        ]
        units = [
            {
                "code": str(row["unit_code"]),
                "label": str(row["unit_name"]),
                "region": self._region_code(tables, row["region_id"]),
            }
            for row in sorted(tables["SDC_DIM_UNIT"], key=lambda item: item["unit_code"])
        ]
        cache = self._repository.snapshot()
        control = self._latest_control(tables)
        cache.update(
            {
                "dataset_name": control.get("dataset_name"),
                "dataset_version": control.get("version_no"),
                "load_id": control.get("load_id"),
                "source_watermark": control.get("source_watermark"),
                "published_at": control.get("published_at"),
                "status": control.get("status"),
            }
        )
        return {
            "weeks": weeks,
            "latest_week": weeks[-1] if weeks else None,
            "regions": regions,
            "units": units,
            "metrics": [
                {"code": code, "label": definition["label"]}
                for code, definition in METRICS.items()
            ],
            "dimensions": [
                {"code": code, "label": label} for code, label in DIMENSIONS.items()
            ],
            "cache": cache,
        }

    def _region_code(self, tables: dict[str, list[dict[str, Any]]], region_id: Any) -> str:
        for row in tables["SDC_DIM_REGION"]:
            if row["region_id"] == region_id:
                return str(row["region_code"])
        return ""

    def _maps(self, tables: dict[str, list[dict[str, Any]]]) -> dict[str, dict[Any, dict[str, Any]]]:
        return {
            "regions": {row["region_id"]: row for row in tables["SDC_DIM_REGION"]},
            "units": {row["unit_id"]: row for row in tables["SDC_DIM_UNIT"]},
            "sellers": {row["seller_id"]: row for row in tables["SDC_DIM_SELLER"]},
            "products": {row["product_id"]: row for row in tables["SDC_DIM_PRODUCT"]},
            "channels": {row["channel_id"]: row for row in tables["SDC_DIM_CHANNEL"]},
        }

    def _latest_control(self, tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        rows = tables["SDC_CONTROL_DATASET_LOAD"]
        return max(rows, key=lambda row: int(row.get("version_no", 0))) if rows else {}

    def _freshness(self, tables: dict[str, list[dict[str, Any]]], week: str) -> dict[str, Any]:
        control = self._latest_control(tables)
        audit = next(
            (row for row in tables["SDC_AUDIT_DATASET_LOAD"] if str(row["snapshot_week"])[:10] == week),
            {},
        )
        audit_status = audit.get("status")
        return {
            "dataset_name": control.get("dataset_name"),
            "version": control.get("version_no"),
            "load_id": control.get("load_id") or audit.get("load_id"),
            "source_watermark": control.get("source_watermark") or audit.get("source_max_date"),
            "published_at": control.get("published_at"),
            "status": audit_status or "MISSING_AUDIT",
            "reconciled": audit_status == "PASS",
        }

    def _resolve_week(self, tables: dict[str, list[dict[str, Any]]], week: str | None) -> tuple[str, list[str]]:
        available = sorted({str(row["week_start"])[:10] for row in tables["SDC_FACT_WEEKLY_TARGET"]})
        if not available:
            raise DataNotReady("No weekly target data is available; run: python seed.py")
        selected = available[-1] if week is None else str(week)[:10]
        if selected not in available:
            raise ValueError(f"Unknown review week {selected}; choose one of the available weeks")
        return selected, available

    @staticmethod
    def _resolve_lookback(lookback: int | str | None) -> int:
        try:
            value = 12 if lookback is None else int(lookback)
        except (TypeError, ValueError) as exc:
            raise ValueError("lookback must be an integer between 4 and 16") from exc
        if not 4 <= value <= 16:
            raise ValueError("lookback must be between 4 and 16")
        return value

    def _filter_ids(
        self,
        tables: dict[str, list[dict[str, Any]]],
        region: str | None,
        unit: str | None,
    ) -> tuple[set[int], set[int]]:
        region_by_code = {str(row["region_code"]): row for row in tables["SDC_DIM_REGION"]}
        unit_by_code = {str(row["unit_code"]): row for row in tables["SDC_DIM_UNIT"]}
        allowed_regions = set(region_by_code)
        if region:
            if region not in region_by_code:
                raise ValueError(f"Unknown region {region}")
            allowed_regions = {region}
        allowed_units = {
            int(row["unit_id"])
            for row in tables["SDC_DIM_UNIT"]
            if self._region_code(tables, row["region_id"]) in allowed_regions
        }
        if unit:
            if unit not in unit_by_code:
                raise ValueError(f"Unknown sales unit {unit}")
            selected_unit_id = int(unit_by_code[unit]["unit_id"])
            if selected_unit_id not in allowed_units:
                raise ValueError(f"Sales unit {unit} is not in region {region}")
            allowed_units = {selected_unit_id}
        seller_ids = {
            int(row["seller_id"])
            for row in tables["SDC_DIM_SELLER"]
            if int(row["unit_id"]) in allowed_units
        }
        return allowed_units, seller_ids

    def _group_fn(
        self,
        maps: dict[str, dict[Any, dict[str, Any]]],
        scope: str,
    ) -> Callable[[dict[str, Any]], tuple[str, str, str]]:
        if scope not in {"network", "region", "unit"}:
            raise ValueError("scope must be network, region, or unit")
        if scope == "unit":
            return lambda row: (
                str(maps["sellers"][row["seller_id"]]["seller_code"]),
                str(maps["sellers"][row["seller_id"]]["full_name"]),
                str(maps["units"][maps["sellers"][row["seller_id"]]["unit_id"]]["unit_name"]),
            )
        if scope == "region":
            return lambda row: (
                str(maps["regions"][maps["units"][row["unit_id"]]["region_id"]]["region_code"]),
                str(maps["regions"][maps["units"][row["unit_id"]]["region_id"]]["region_name"]),
                "Network",
            )
        return lambda row: (
            str(maps["units"][row["unit_id"]]["unit_code"]),
            str(maps["units"][row["unit_id"]]["unit_name"]),
            str(maps["regions"][maps["units"][row["unit_id"]]["region_id"]]["region_name"]),
        )

    @staticmethod
    def _validate_scope(scope: str, unit: str | None) -> None:
        if scope not in {"network", "region", "unit"}:
            raise ValueError("scope must be network, region, or unit")
        if scope == "unit" and not unit:
            raise ValueError("unit is required when scope=unit")

    def _aggregate(
        self,
        rows: list[dict[str, Any]],
        group_fn: Callable[[dict[str, Any]], tuple[str, str, str]],
        source: str,
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        fields = (
            ("sales_count", "sales_count", 0),
            ("sales_amount", "sales_amount", 0.0),
            ("margin_amount", "margin_amount", 0.0),
            ("leads", "leads", 0),
            ("qualified_leads", "qualified_leads", 0),
            ("wins", "wins", 0),
            ("weighted_pipeline", "weighted_pipeline_value", 0.0),
            ("pipeline", "pipeline_value", 0.0),
            ("cancelled_amount", "cancelled_amount", 0.0),
        )
        target_fields = {
            "target_count": "target_count",
            "target_amount": "target_amount",
            "target_margin": "target_margin",
        }
        for row in rows:
            code, label, parent = group_fn(row)
            item = result.setdefault(
                code,
                {"code": code, "label": label, "parent": parent, "source": source},
            )
            if source == "actual":
                for key, field, default in fields:
                    value = row.get(field, default)
                    item[key] = item.get(key, 0) + (self._as_int(value) if isinstance(default, int) else self._as_float(value))
            else:
                for key, field in target_fields.items():
                    item[key] = item.get(key, 0) + self._as_float(row.get(field))
        return result

    @staticmethod
    def _merge(actual: dict[str, Any] | None, target: dict[str, Any] | None) -> dict[str, Any]:
        result = dict(actual or {})
        if target:
            for key, value in target.items():
                if key not in {"code", "label", "parent", "source"}:
                    result[key] = value
            for key in ("code", "label", "parent"):
                result.setdefault(key, target.get(key))
        return result

    def _severity(self, forecast: float, coverage: float | None, target: float) -> str:
        if target <= 0:
            return "neutral"
        if forecast < 0.90 or (coverage is not None and coverage < 0.80):
            return "critical"
        if forecast < 1.0 or (coverage is not None and coverage < 1.0):
            return "warning"
        return "good"

    @staticmethod
    def _issue_and_action(severity: str, forecast: float, coverage: float | None) -> tuple[str, str]:
        if severity == "critical":
            if coverage is not None and coverage < 0.80:
                return "Insufficient pipeline to recover the target", "Create a recovery plan and inspect the weakest channel"
            return "Forecast is materially below target", "Review the largest negative driver and reallocate near-term activity"
        if severity == "warning":
            if coverage is not None and coverage < 1.0:
                return "Target is at risk with limited pipeline cover", "Check open opportunities and schedule a focused pipeline review"
            return "Run-rate is below target", "Monitor the next weekly close and address the leading indicator first"
        if severity == "good":
            return "On track", "Protect the current pace and share the repeatable driver"
        return "No target available", "Validate the target before taking action"

    def _rows_for_scope(
        self,
        tables: dict[str, list[dict[str, Any]]],
        week: str,
        allowed_units: set[int],
        allowed_sellers: set[int],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        actual = [
            row for row in tables["SDC_FACT_WEEKLY_SCORECARD"]
            if str(row["week_start"])[:10] == week
            and int(row["unit_id"]) in allowed_units
            and int(row["seller_id"]) in allowed_sellers
        ]
        target = [
            row for row in tables["SDC_FACT_WEEKLY_TARGET"]
            if str(row["week_start"])[:10] == week
            and int(row["unit_id"]) in allowed_units
            and int(row["seller_id"]) in allowed_sellers
        ]
        return actual, target

    def _rolling_rows(
        self,
        rows: list[dict[str, Any]],
        selected_week: str,
        lookback: int,
        allowed_units: set[int],
        allowed_sellers: set[int],
    ) -> list[dict[str, Any]]:
        selected = self._date(selected_week)
        start = selected - dt.timedelta(days=(lookback - 1) * 7)
        return [
            row for row in rows
            if start <= self._date(row["week_start"]) <= selected
            and int(row["unit_id"]) in allowed_units
            and int(row["seller_id"]) in allowed_sellers
        ]

    @staticmethod
    def _value(item: dict[str, Any], metric: str, source: str) -> float:
        definition = METRICS[metric]
        return float(item.get(definition[source], 0) or 0)

    def _trend(
        self,
        actual_rows: list[dict[str, Any]],
        target_rows: list[dict[str, Any]],
        selected_week: str,
        lookback: int,
        metric: str,
        allowed_units: set[int],
        allowed_sellers: set[int],
    ) -> dict[str, Any]:
        selected = self._date(selected_week)
        weeks = [selected - dt.timedelta(days=(lookback - index - 1) * 7) for index in range(lookback)]
        labels = [week.isoformat() for week in weeks]
        actual_values: list[float] = []
        target_values: list[float] = []
        for week in labels:
            a = sum(
                self._value(row, metric, "actual")
                for row in actual_rows
                if str(row["week_start"])[:10] == week
                and int(row["unit_id"]) in allowed_units
                and int(row["seller_id"]) in allowed_sellers
            )
            t = sum(
                self._value(row, metric, "target")
                for row in target_rows
                if str(row["week_start"])[:10] == week
                and int(row["unit_id"]) in allowed_units
                and int(row["seller_id"]) in allowed_sellers
            )
            actual_values.append(round(a, 2))
            target_values.append(round(t, 2))
        return {
            "id": "trend",
            "type": "line",
            "title": f"{METRICS[metric]['label']} — trailing {lookback} weeks",
            "labels": labels,
            "series": [
                {"name": "Actual", "data": actual_values},
                {"name": "Target", "data": target_values},
            ],
        }

    async def cockpit(
        self,
        week: str | None = None,
        lookback: int | str | None = None,
        scope: str = "network",
        region: str | None = None,
        unit: str | None = None,
        metric: str = "sales_amount",
    ) -> dict[str, Any]:
        tables = await self._tables()
        selected_week, _available = self._resolve_week(tables, week)
        resolved_lookback = self._resolve_lookback(lookback)
        if metric not in METRICS:
            raise ValueError(f"metric must be one of: {', '.join(METRICS)}")
        self._validate_scope(scope, unit)
        maps = self._maps(tables)
        allowed_units, allowed_sellers = self._filter_ids(tables, region, unit)
        actual, target = self._rows_for_scope(tables, selected_week, allowed_units, allowed_sellers)
        group_fn = self._group_fn(maps, scope)
        actual_groups = self._aggregate(actual, group_fn, "actual")
        target_groups = self._aggregate(target, group_fn, "target")
        rolling_actual = self._rolling_rows(
            tables["SDC_FACT_WEEKLY_SCORECARD"], selected_week, min(4, resolved_lookback), allowed_units, allowed_sellers
        )
        rolling_target = self._rolling_rows(
            tables["SDC_FACT_WEEKLY_TARGET"], selected_week, min(4, resolved_lookback), allowed_units, allowed_sellers
        )
        rolling_actual_groups = self._aggregate(rolling_actual, group_fn, "actual")
        rolling_target_groups = self._aggregate(rolling_target, group_fn, "target")
        exceptions: list[dict[str, Any]] = []
        for code in sorted(set(actual_groups) | set(target_groups)):
            item = self._merge(actual_groups.get(code), target_groups.get(code))
            rolling_item = self._merge(rolling_actual_groups.get(code), rolling_target_groups.get(code))
            actual_value = self._value(item, metric, "actual")
            target_value = self._value(item, metric, "target")
            rolling_actual_value = self._value(rolling_item, metric, "actual")
            rolling_target_value = self._value(rolling_item, metric, "target")
            attainment = actual_value / target_value if target_value else 0.0
            forecast = rolling_actual_value / rolling_target_value if rolling_target_value else attainment
            remaining_target = max(self._as_float(item.get("target_amount")) - self._as_float(item.get("sales_amount")), 0.0)
            coverage = self._as_float(item.get("weighted_pipeline")) / remaining_target if remaining_target else 2.0
            leads = self._as_int(item.get("leads"))
            wins = self._as_int(item.get("wins"))
            sales_amount = self._as_float(item.get("sales_amount"))
            cancelled = self._as_float(item.get("cancelled_amount"))
            conversion = wins / leads if leads else None
            cancellation = cancelled / (sales_amount + cancelled) if sales_amount + cancelled else None
            severity = self._severity(forecast, coverage, target_value)
            issue, action = self._issue_and_action(severity, forecast, coverage)
            exceptions.append(
                {
                    "code": code,
                    "entity": item.get("label", code),
                    "parent": item.get("parent", ""),
                    "actual": round(actual_value, 2),
                    "target": round(target_value, 2),
                    "gap": round(actual_value - target_value, 2),
                    "gap_pct": round((actual_value - target_value) / target_value, 4) if target_value else 0.0,
                    "attainment": round(attainment, 4),
                    "forecast_attainment": round(forecast, 4),
                    "pipeline_coverage": round(coverage, 4),
                    "conversion_rate": round(conversion, 4) if conversion is not None else None,
                    "cancellation_rate": round(cancellation, 4) if cancellation is not None else None,
                    "severity": severity,
                    "issue": issue,
                    "action": action,
                }
            )
        order = {"critical": 0, "warning": 1, "neutral": 2, "good": 3}
        exceptions.sort(key=lambda row: (order.get(row["severity"], 9), row["gap"], row["entity"]))

        total_actual = sum(self._value(row, metric, "actual") for row in actual)
        total_target = sum(self._value(row, metric, "target") for row in target)
        rolling_total_actual = sum(self._value(row, metric, "actual") for row in rolling_actual)
        rolling_total_target = sum(self._value(row, metric, "target") for row in rolling_target)
        total_pipeline = sum(self._as_float(row.get("weighted_pipeline_value")) for row in actual)
        total_leads = sum(self._as_int(row.get("leads")) for row in actual)
        total_wins = sum(self._as_int(row.get("wins")) for row in actual)
        total_cancelled = sum(self._as_float(row.get("cancelled_amount")) for row in actual)
        sales_amount = sum(self._as_float(row.get("sales_amount")) for row in actual)
        attainment = total_actual / total_target if total_target else 0.0
        rolling_attainment = rolling_total_actual / rolling_total_target if rolling_total_target else attainment
        total_remaining = max(
            sum(self._as_float(row.get("target_amount")) for row in target) - sales_amount,
            0.0,
        )
        pipeline_coverage = total_pipeline / total_remaining if total_remaining else 2.0
        conversion = total_wins / total_leads if total_leads else 0.0
        cancellation = total_cancelled / (sales_amount + total_cancelled) if sales_amount + total_cancelled else 0.0
        headlines = [
            {"key": "actual", "label": f"{METRICS[metric]['label']} actual", "value": round(total_actual, 2), "fmt": METRICS[metric]["fmt"], "detail": f"Review week {selected_week}", "status": "neutral"},
            {"key": "target", "label": "Target", "value": round(total_target, 2), "fmt": METRICS[metric]["fmt"], "detail": "Selected scope", "status": "neutral"},
            {"key": "attainment", "label": "Attainment", "value": round(attainment, 4), "fmt": "pct", "detail": f"Gap {self._format_gap(total_actual - total_target, METRICS[metric]['fmt'])}", "status": "good" if attainment >= 1 else "warning"},
            {"key": "pace", "label": "4-week pace", "value": round(rolling_attainment, 4), "fmt": "pct", "detail": "Rolling actual vs target", "status": "good" if rolling_attainment >= 1 else "warning"},
            {"key": "pipeline", "label": "Pipeline cover", "value": round(pipeline_coverage, 4), "fmt": "pct", "detail": "Weighted pipeline / remaining target", "status": "good" if pipeline_coverage >= 1 else "warning"},
            {"key": "conversion", "label": "Lead conversion", "value": round(conversion, 4), "fmt": "pct", "detail": f"{total_wins:,} wins from {total_leads:,} leads", "status": "neutral"},
            {"key": "cancellation", "label": "Cancellation rate", "value": round(cancellation, 4), "fmt": "pct", "detail": "Cancelled amount / gross booked", "status": "good" if cancellation < 0.06 else "warning"},
        ]
        critical = sum(row["severity"] == "critical" for row in exceptions)
        warning = sum(row["severity"] == "warning" for row in exceptions)
        good = sum(row["severity"] == "good" for row in exceptions)
        narrative = self._narrative(exceptions, total_actual, total_target, pipeline_coverage, metric)
        trend = self._trend(
            tables["SDC_FACT_WEEKLY_SCORECARD"],
            tables["SDC_FACT_WEEKLY_TARGET"],
            selected_week,
            resolved_lookback,
            metric,
            allowed_units,
            allowed_sellers,
        )
        return {
            "week": selected_week,
            "lookback": resolved_lookback,
            "scope": scope,
            "region": region,
            "unit": unit,
            "metric": metric,
            "subtitle": f"Closed week {selected_week} · trailing {resolved_lookback} weeks · {self._scope_label(scope, region, unit)}",
            "headlines": headlines,
            "status_counts": {"critical": critical, "warning": warning, "good": good, "neutral": len(exceptions) - critical - warning - good},
            "exceptions": exceptions,
            "narrative": narrative,
            "charts": [trend, {"id": "status", "type": "doughnut", "title": "Exception mix", "labels": ["Critical", "Watch", "On track"], "series": [{"name": "Units", "data": [critical, warning, good]}]}],
            "freshness": self._freshness(tables, selected_week),
        }

    @staticmethod
    def _format_gap(value: float, fmt: str) -> str:
        if fmt == "eur":
            return f"€{value:,.0f}"
        return f"{value:,.0f}"

    @staticmethod
    def _scope_label(scope: str, region: str | None, unit: str | None) -> str:
        if unit:
            return unit
        if region:
            return region
        return {"network": "all sales units", "region": "regional view", "unit": "selected unit"}.get(scope, scope)

    def _narrative(
        self,
        exceptions: list[dict[str, Any]],
        actual: float,
        target: float,
        coverage: float,
        metric: str,
    ) -> list[dict[str, str]]:
        critical = next((row for row in exceptions if row["severity"] == "critical"), None)
        warning = next((row for row in exceptions if row["severity"] == "warning"), None)
        if critical:
            decision = "Intervene this week"
            detail = f"{critical['entity']} is the first exception to review: {critical['issue'].lower()} ({critical['gap_pct']:.1%} vs target)."
            action = critical["action"]
        elif warning:
            decision = "Watch the leading indicators"
            detail = f"The network is close to plan, but {warning['entity']} has the largest watch item: {warning['issue'].lower()}."
            action = warning["action"]
        else:
            decision = "Maintain course"
            detail = "No unit is outside the configured intervention thresholds for this review week."
            action = "Share the strongest repeatable driver at the next operating review."
        gap = actual - target
        return [
            {"kind": "Decision", "title": decision, "detail": detail},
            {"kind": "Evidence", "title": f"{METRICS[metric]['label']} is {gap / target:.1%} vs target" if target else "Target is unavailable", "detail": f"Actual {self._format_gap(actual, METRICS[metric]['fmt'])} against {self._format_gap(target, METRICS[metric]['fmt'])}; pipeline cover is {coverage:.0%}."},
            {"kind": "Next action", "title": action, "detail": "The recommendation is generated from the exception rule and remains read-only in this demo."},
        ]

    def _dimension_group_fn(
        self,
        maps: dict[str, dict[Any, dict[str, Any]]],
        dimension: str,
    ) -> Callable[[dict[str, Any]], tuple[str, str, str]]:
        if dimension not in DIMENSIONS:
            raise ValueError(f"dimension must be one of: {', '.join(DIMENSIONS)}")
        if dimension == "product":
            return lambda row: (str(row["product_id"]), str(maps["products"][row["product_id"]]["product_name"]), str(maps["products"][row["product_id"]]["product_group"]))
        if dimension == "channel":
            return lambda row: (str(row["channel_id"]), str(maps["channels"][row["channel_id"]]["channel_name"]), "Sales channel")
        if dimension == "region":
            return lambda row: (str(maps["regions"][maps["units"][row["unit_id"]]["region_id"]]["region_code"]), str(maps["regions"][maps["units"][row["unit_id"]]["region_id"]]["region_name"]), "Network")
        return lambda row: (str(maps["units"][row["unit_id"]]["unit_code"]), str(maps["units"][row["unit_id"]]["unit_name"]), str(maps["regions"][maps["units"][row["unit_id"]]["region_id"]]["region_name"]))

    async def drivers(
        self,
        week: str | None = None,
        lookback: int | str | None = None,
        scope: str = "network",
        region: str | None = None,
        unit: str | None = None,
        metric: str = "sales_amount",
        dimension: str = "product",
    ) -> dict[str, Any]:
        tables = await self._tables()
        selected_week, _available = self._resolve_week(tables, week)
        resolved_lookback = self._resolve_lookback(lookback)
        if metric not in METRICS:
            raise ValueError(f"metric must be one of: {', '.join(METRICS)}")
        self._validate_scope(scope, unit)
        maps = self._maps(tables)
        allowed_units, allowed_sellers = self._filter_ids(tables, region, unit)
        actual, target = self._rows_for_scope(tables, selected_week, allowed_units, allowed_sellers)
        group_fn = self._dimension_group_fn(maps, dimension)
        actual_groups = self._aggregate(actual, group_fn, "actual")
        target_groups = self._aggregate(target, group_fn, "target")
        total_actual = sum(self._value(row, metric, "actual") for row in actual)
        total_target = sum(self._value(row, metric, "target") for row in target)
        total_gap = total_actual - total_target
        drivers: list[dict[str, Any]] = []
        for code in sorted(set(actual_groups) | set(target_groups)):
            item = self._merge(actual_groups.get(code), target_groups.get(code))
            actual_value = self._value(item, metric, "actual")
            target_value = self._value(item, metric, "target")
            leads = self._as_int(item.get("leads"))
            wins = self._as_int(item.get("wins"))
            sales_amount = self._as_float(item.get("sales_amount"))
            cancelled = self._as_float(item.get("cancelled_amount"))
            drivers.append(
                {
                    "code": code,
                    "label": item.get("label", code),
                    "actual": round(actual_value, 2),
                    "target": round(target_value, 2),
                    "contribution": round(actual_value - target_value, 2),
                    "contribution_pct": round((actual_value - target_value) / abs(total_gap), 4) if total_gap else 0.0,
                    "leads": leads,
                    "wins": wins,
                    "conversion_rate": round(wins / leads, 4) if leads else None,
                    "weighted_pipeline": round(self._as_float(item.get("weighted_pipeline")), 2),
                    "cancellation_rate": round(cancelled / (sales_amount + cancelled), 4) if sales_amount + cancelled else None,
                }
            )
        drivers.sort(key=lambda row: (row["contribution"], row["label"]))
        trend = self._trend(
            tables["SDC_FACT_WEEKLY_SCORECARD"],
            tables["SDC_FACT_WEEKLY_TARGET"],
            selected_week,
            resolved_lookback,
            metric,
            allowed_units,
            allowed_sellers,
        )
        return {
            "week": selected_week,
            "lookback": resolved_lookback,
            "scope": scope,
            "region": region,
            "unit": unit,
            "metric": metric,
            "dimension": dimension,
            "title": f"Why {METRICS[metric]['label'].lower()} is above or below target",
            "total_actual": round(total_actual, 2),
            "total_target": round(total_target, 2),
            "total_gap": round(total_gap, 2),
            "bridge": [
                {"label": "Target", "value": round(total_target, 2), "kind": "base"},
                *[{"label": row["label"], "code": row["code"], "value": row["contribution"], "kind": "delta"} for row in drivers],
                {"label": "Actual", "value": round(total_actual, 2), "kind": "total"},
            ],
            "drivers": drivers,
            "charts": [
                trend,
                {"id": "drivers", "type": "bar", "title": "Largest contribution to the gap", "labels": [row["label"] for row in drivers[:8]], "series": [{"name": "Contribution", "data": [row["contribution"] for row in drivers[:8]]}]},
            ],
            "freshness": self._freshness(tables, selected_week),
        }

    async def review_pack(
        self,
        week: str | None = None,
        scope: str = "network",
        region: str | None = None,
        unit: str | None = None,
        metric: str = "sales_amount",
    ) -> dict[str, Any]:
        cockpit = await self.cockpit(week, 12, scope, region, unit, metric)
        exceptions = cockpit["exceptions"]
        selected = next((row for row in exceptions if row["severity"] in {"critical", "warning"}), None)
        if selected:
            status = selected["severity"]
            decision_title = "Focus the next review on the first exception"
            decision_detail = f"{selected['entity']}: {selected['issue']}."
            actions = [
                {"priority": selected["severity"], "owner": selected["parent"], "action": selected["action"], "due": (self._date(cockpit["week"]) + dt.timedelta(days=7)).isoformat()},
                {"priority": "follow-up", "owner": selected["entity"], "action": "Bring the leading-indicator trend and recovery evidence to the next review", "due": (self._date(cockpit["week"]) + dt.timedelta(days=14)).isoformat()},
            ]
        else:
            status = "good"
            decision_title = "Maintain course and codify the winning pattern"
            decision_detail = "No critical or warning exception requires intervention in the selected scope."
            actions = [
                {"priority": "good", "owner": "Sales leadership", "action": "Capture the strongest repeatable driver for the next operating review", "due": (self._date(cockpit["week"]) + dt.timedelta(days=7)).isoformat()},
            ]
        headline_by_key = {row["key"]: row for row in cockpit["headlines"]}
        evidence = [
            {"label": "Decision status", "value": status.title()},
            {"label": "Attainment", "value": f"{headline_by_key['attainment']['value']:.1%}"},
            {"label": "Four-week pace", "value": f"{headline_by_key['pace']['value']:.1%}"},
            {"label": "Pipeline cover", "value": f"{headline_by_key['pipeline']['value']:.1%}"},
        ]
        return {
            "week": cockpit["week"],
            "title": "Weekly sales operating review",
            "subtitle": cockpit["subtitle"],
            "decision": {"status": status, "title": decision_title, "detail": decision_detail},
            "evidence": evidence,
            "recommended_actions": actions,
            "exceptions": exceptions[:5],
            "freshness": cockpit["freshness"],
        }
