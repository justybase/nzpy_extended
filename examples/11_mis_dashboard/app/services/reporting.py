"""
Report definitions for the MIS dashboard.

Everything is computed in pure Python over the fully cached MIS_* tables
(see cache_service.py) — Netezza is never queried per user request.
Each report builds:
    kpis       - headline KPI cards
    synthetic  - monthly summary table (with MoM deltas)
    analytic   - drill-down table by a chosen dimension (branch/region/advisor/...)
    charts     - Chart.js payloads (line / bar / doughnut / horizontal bar)

Money is formatted as EUR on the frontend; pct values are already multiplied
by 100 (e.g. 12.34 means 12.34%).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

MONTHS_EN = ["January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]


def month_label(ym: str) -> str:
    year, month = ym.split("-")
    return f"{MONTHS_EN[int(month) - 1]} {year}"


def prev_month(ym: str) -> str:
    year, month = int(ym[:4]), int(ym[5:7])
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def shift_month(ym: str, offset: int) -> str:
    """Return ``ym`` shifted by a signed number of calendar months."""
    year, month = int(ym[:4]), int(ym[5:7])
    absolute = year * 12 + month - 1 + offset
    return f"{absolute // 12:04d}-{absolute % 12 + 1:02d}"


def month_sequence(from_ym: str, to_ym: str) -> list[str]:
    """Return every month in an inclusive range, including empty months."""
    count = (int(to_ym[:4]) * 12 + int(to_ym[5:7])) - (
        int(from_ym[:4]) * 12 + int(from_ym[5:7]))
    return [shift_month(from_ym, i) for i in range(max(0, count) + 1)]


def comparable_period(from_ym: str, to_ym: str) -> tuple[str, str]:
    """Return the immediately preceding period with the same month count."""
    count = len(month_sequence(from_ym, to_ym))
    previous_to = prev_month(from_ym)
    return shift_month(previous_to, -(count - 1)), previous_to


def col(key: str, label: str, fmt: str = "str") -> dict[str, str]:
    return {"key": key, "label": label, "fmt": fmt}


def table(columns: list[dict[str, str]], rows: list[list[Any]]) -> dict[str, Any]:
    return {"columns": columns, "rows": rows}


def series(name: str, data: list[Any]) -> dict[str, Any]:
    return {"name": name, "data": data}


def chart(cid: str, ctype: str, title: str, labels: list[str],
          series_list: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": cid, "type": ctype, "title": title, "labels": labels,
            "series": series_list}


class UnknownReport(KeyError):
    pass


class DataNotReady(RuntimeError):
    pass


def aggregate(rows: Any, key_idxs: list[int], measures: list[tuple[str, str, int | None]],
              pred: Callable[[list[Any]], bool] | None = None,
              key_fn: Callable[[list[Any]], tuple] | None = None) -> list[dict[str, Any]]:
    """
    Group rows by `key_idxs` and compute `measures`.

    measures: (name, kind, col_idx) with kind in:
        sum / avg / count / countd / max / min
    Returns a list of {"__key": (k1, k2, ...), <name>: value, ...} in first-seen order.
    """
    acc: dict[tuple, dict[str, Any]] = {}
    order: list[tuple] = []
    for r in rows:
        if pred is not None and not pred(r):
            continue
        key = key_fn(r) if key_fn is not None else tuple(r[i] for i in key_idxs)
        d = acc.get(key)
        if d is None:
            d = {"__n": 0}
            acc[key] = d
            order.append(key)
        d["__n"] += 1
        for name, kind, ci in measures:
            if kind == "count":
                d[name] = d.get(name, 0) + 1
            elif kind == "countd":
                d.setdefault(name + "~set", set()).add(r[ci])
            elif kind == "sum" or kind == "avg":
                v = r[ci]
                if v is not None:
                    d[name] = d.get(name, 0) + v
                    if kind == "avg":
                        d[name + "~n"] = d.get(name + "~n", 0) + 1
            elif kind == "max":
                v = r[ci]
                if v is not None and v > d.get(name, float("-inf")):
                    d[name] = v
            elif kind == "min":
                v = r[ci]
                if v is not None and v < d.get(name, float("inf")):
                    d[name] = v
    out: list[dict[str, Any]] = []
    for key in order:
        d = acc[key]
        row: dict[str, Any] = {"__key": key}
        for name, kind, ci in measures:
            if kind == "avg":
                n = d.get(name + "~n", 0)
                row[name] = round(d.get(name, 0.0) / n, 2) if n else None
            elif kind == "countd":
                row[name] = len(d[name + "~set"])
            else:
                row[name] = d.get(name)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Cached-table access + dimension maps
# ---------------------------------------------------------------------------

async def build_maps(cache: Any) -> dict[str, dict[int, dict[str, Any]]]:
    """Row-by-id lookup maps for every dimension table (products, branches, ...)."""

    async def to_map(table_name: str, key_col: str) -> dict[int, dict[str, Any]]:
        cols, rows = await cache.get_table(table_name)
        ki = cols.index(key_col)
        return {row[ki]: dict(zip(cols, row)) for row in rows}

    return {
        "products": await to_map("MIS_DIM_PRODUCT", "product_id"),
        "branches": await to_map("MIS_DIM_BRANCH", "branch_id"),
        "regions": await to_map("MIS_DIM_REGION", "region_id"),
        "advisors": await to_map("MIS_DIM_ADVISOR", "advisor_id"),
        "channels": await to_map("MIS_DIM_CHANNEL", "channel_id"),
        "campaigns": await to_map("MIS_DIM_CAMPAIGN", "campaign_id"),
    }


async def available_months(cache: Any) -> list[str]:
    cols, rows = await cache.get_table("MIS_FACT_SALES")
    di = cols.index("sale_date")
    return sorted({r[di][:7] for r in rows})


def filtered_sales(cols: list[str], rows: list[list[Any]], maps: dict[str, Any],
                   from_ym: str, to_ym: str, group: str | None = None,
                   status: str = "BOOKED", branch_id: int | None = None,
                   advisor_id: int | None = None) -> Any:
    """Generator of booked sales rows within the requested period.

    The sale_date cell is normalized to 'YYYY-MM' so downstream aggregations
    keyed by it naturally group by month. Optional branch/advisor filters are
    used by the drill-down queries.
    """
    di, pi, si = cols.index("sale_date"), cols.index("product_id"), cols.index("sale_status")
    bi, ai = cols.index("branch_id"), cols.index("advisor_id")
    for r in rows:
        if status and r[si] != status:
            continue
        if branch_id is not None and r[bi] != branch_id:
            continue
        if advisor_id is not None and r[ai] != advisor_id:
            continue
        ym = r[di][:7]
        if ym < from_ym or ym > to_ym:
            continue
        if group and maps["products"][r[pi]]["product_group"] != group:
            continue
        if r[di] != ym:
            r = list(r)
            r[di] = ym
        yield r


def month_rows(agg_rows: list[dict[str, Any]], measure_names: list[str],
               mom_measure: str | None = None,
               months: list[str] | None = None) -> list[list[Any]]:
    """Convert aggregated rows into display rows sorted by month."""
    by_month = {r["__key"][0]: r for r in agg_rows}
    selected_months = months or sorted(by_month)
    out: list[list[Any]] = []
    for ym in selected_months:
        ar = by_month.get(ym, {})
        row: list[Any] = [ym, month_label(ym)]
        for m in measure_names:
            # Counts and sums have a meaningful zero for an empty month;
            # averages remain null because no denominator exists.
            row.append(ar.get(m, None if m.startswith("avg") else 0))
        out.append(row)
    if mom_measure:
        prev: Any = None
        for row in out:
            v = row[2 + measure_names.index(mom_measure)]
            row.append(round((v - prev) / prev * 100, 2) if prev else None)
            prev = v
    return out


def decorate(agg_rows: list[dict[str, Any]], maps: dict[str, Any], dim: str,
             measure_names: list[str]) -> list[list[Any]]:
    """Turn aggregated rows keyed by a dimension id into labeled display rows."""
    out: list[list[Any]] = []
    for ar in agg_rows:
        key = ar["__key"][0]
        if dim == "branch":
            b = maps["branches"].get(key, {})
            r = maps["regions"].get(b.get("region_id"), {})
            row: list[Any] = [b.get("branch_code"), b.get("branch_name"),
                              r.get("region_name"), b.get("city")]
        elif dim == "region":
            r = maps["regions"].get(key, {})
            row = [r.get("region_code"), r.get("region_name"), r.get("area_name")]
        elif dim == "advisor":
            a = maps["advisors"].get(key, {})
            b = maps["branches"].get(a.get("branch_id"), {})
            row = [a.get("advisor_code"), f"{a.get('first_name')} {a.get('last_name')}",
                   a.get("role"), b.get("branch_name")]
        elif dim == "product":
            p = maps["products"].get(key, {})
            row = [p.get("product_code"), p.get("product_name"),
                   p.get("product_subgroup"), p.get("product_group")]
        elif dim == "channel":
            c = maps["channels"].get(key, {})
            row = [c.get("channel_name")]
        else:
            row = [key]
        for m in measure_names:
            row.append(ar.get(m))
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Shared builders: sales, balances, movement, campaigns
# ---------------------------------------------------------------------------

SALES_MEASURES = [
    ("cnt", "count", None),
    ("amount", "sum", 7),     # amount column index resolved per table below
    ("avg_ticket", "avg", 7),
    ("commission", "sum", 8),
]


def _sales_measures(cols: list[str]) -> list[tuple[str, str, int | None]]:
    ai, ci = cols.index("amount"), cols.index("commission")
    return [("cnt", "count", None), ("amount", "sum", ai),
            ("avg_ticket", "avg", ai), ("commission", "sum", ci)]


def make_sales_monthly(group: str | None = None, mom_measure: str = "amount") -> Callable:
    """Synthetic table factory: monthly sales summary for a product group."""

    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_SALES")
        agg = aggregate(filtered_sales(cols, rows, maps, from_ym, to_ym, group),
                        [cols.index("sale_date")], _sales_measures(cols))
        measures = ["cnt", "amount", "avg_ticket", "commission"]
        data = month_rows(agg, measures, mom_measure,
                          month_sequence(from_ym, to_ym))
        columns = [col("ym", "Month", "str"),
                   col("month", "Month", "str"),
                   col("cnt", "Count", "int"),
                   col("amount", "Amount (EUR)", "eur"),
                   col("avg_ticket", "Avg amount (EUR)", "eur"),
                   col("commission", "Commission (EUR)", "eur"),
                   col("mom_pct", "MoM change %", "pct")]
        return table(columns, data)

    return build


def make_sales_analytic(group: str | None = None, top: int | None = None) -> Callable:
    """Analytic table factory: drill-down by dimension for a product group."""

    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str,
                    dim: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_SALES")
        key_idx = {"branch": cols.index("branch_id"), "region": cols.index("branch_id"),
                   "advisor": cols.index("advisor_id"), "product": cols.index("product_id"),
                   "channel": cols.index("channel_id")}[dim]
        agg = aggregate(
            filtered_sales(cols, rows, maps, from_ym, to_ym, group),
            [key_idx],
            _sales_measures(cols),
        )
        if dim == "region":
            # remap branch keys -> region keys
            remapped: dict[tuple, list[Any]] = {}
            order: list[tuple] = []
            for ar in agg:
                rid = maps["branches"][ar["__key"][0]]["region_id"]
                key = (rid,)
                d = remapped.get(key)
                if d is None:
                    d = [0, 0.0, 0.0, 0.0]
                    remapped[key] = d
                    order.append(key)
                d[0] += ar.get("cnt") or 0
                d[1] += ar.get("amount") or 0.0
                d[2] += ar.get("avg_ticket") or 0.0
                d[3] += ar.get("commission") or 0.0
            agg = [{"__key": k, "cnt": v[0], "amount": v[1],
                    "avg_ticket": round(v[2] / v[0], 2) if v[0] else None,
                    "commission": v[3]} for k, v in zip(order, (remapped[k] for k in order))]
        agg.sort(key=lambda ar: ar.get("amount") or 0, reverse=True)
        if top:
            agg = agg[:top]
        measures = ["cnt", "amount", "avg_ticket", "commission"]
        data = decorate(agg, maps, dim, measures)

        head = {
            "branch": [col("branch_code", "Code"), col("branch_name", "Branch"),
                       col("region_name", "Region"), col("city", "City")],
            "region": [col("region_code", "Code"), col("region_name", "Region"),
                       col("area_name", "Area")],
            "advisor": [col("advisor_code", "Code"), col("advisor_name", "Advisor"),
                        col("role", "Role"), col("branch_name", "Branch")],
            "product": [col("product_code", "Code"), col("product_name", "Product"),
                        col("product_subgroup", "Subgroup"), col("product_group", "Group")],
            "channel": [col("channel_name", "Channel")],
        }[dim]
        columns = head + [col("cnt", "Count", "int"), col("amount", "Amount (EUR)", "eur"),
                          col("avg_ticket", "Avg amount (EUR)", "eur"),
                          col("commission", "Commission (EUR)", "eur")]
        return table(columns, data)

    return build


def make_balances_monthly(groups: list[str]) -> Callable:
    """Synthetic table factory: monthly balance snapshots for the given groups."""

    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_BALANCES")
        mi, pi, ci, ai = (cols.index("balance_month"), cols.index("product_id"),
                          cols.index("account_count"), cols.index("balance_amount"))
        pred = lambda r: (from_ym <= r[mi][:7] <= to_ym
                          and maps["products"][r[pi]]["product_group"] in groups)
        agg = aggregate(rows, [mi], [("accounts", "sum", ci), ("balance", "sum", ai)],
                        pred, key_fn=lambda r: (r[mi][:7],))
        data = month_rows(agg, ["accounts", "balance"], "balance",
                          month_sequence(from_ym, to_ym))
        columns = [col("ym", "Month", "str"), col("month", "Month", "str"),
                   col("accounts", "Accounts", "int"),
                   col("balance", "Balance (EUR)", "eur"),
                   col("avg_balance", "Avg balance (EUR)", "eur"),
                   col("mom_pct", "MoM change %", "pct")]
        # add avg balance column
        for row in data:
            accounts = row[2]
            row.insert(4, round(row[3] / accounts, 2) if accounts else None)
        return table(columns, data)

    return build


def make_balances_analytic(groups: list[str]) -> Callable:
    """Analytic table factory: snapshot at period end with MoM change, by branch/region."""

    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str,
                    dim: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_BALANCES")
        mi, bi, pi, ci, ai = (cols.index("balance_month"), cols.index("branch_id"),
                              cols.index("product_id"), cols.index("account_count"),
                              cols.index("balance_amount"))
        end_ym, prev_ym = to_ym, prev_month(to_ym)
        by_branch: dict[int, list[Any]] = {}
        for r in rows:
            if maps["products"][r[pi]]["product_group"] not in groups:
                continue
            ym = r[mi][:7]
            if ym == end_ym or ym == prev_ym:
                by_branch.setdefault(r[bi], [0, 0.0, 0, 0.0])
                e = by_branch[r[bi]]
                if ym == end_ym:
                    e[0] += r[ci]
                    e[1] += r[ai]
                else:
                    e[2] += r[ci]
                    e[3] += r[ai]
        data: list[list[Any]] = []
        for bid, (cnt, bal, pcnt, pbal) in by_branch.items():
            b = maps["branches"].get(bid, {})
            r = maps["regions"].get(b.get("region_id"), {})
            if dim == "region":
                key_row = [r.get("region_code"), r.get("region_name"), r.get("area_name")]
            else:
                key_row = [b.get("branch_code"), b.get("branch_name"),
                           r.get("region_name"), b.get("city")]
            change = bal - pbal if pbal else None
            change_pct = round(change / pbal * 100, 2) if pbal else None
            data.append(key_row + [cnt, round(bal, 2), round(pbal, 2),
                                   round(change, 2), change_pct])
        if dim == "region":
            # merge branches of the same region
            merged: dict[str, list[Any]] = {}
            for row in data:
                k = row[0]
                if k not in merged:
                    merged[k] = [row[0], row[1], row[2], 0, 0.0, 0.0, 0.0, 0.0]
                m = merged[k]
                m[3] += row[3]
                m[4] += row[4]
                m[5] += row[5]
                m[6] += row[6]
            data = []
            for m in merged.values():
                change_pct = round(m[6] / m[5] * 100, 2) if m[5] else None
                data.append([m[0], m[1], m[2], m[3], round(m[4], 2), round(m[5], 2),
                             round(m[6], 2), change_pct])
        data.sort(key=lambda row: row[4], reverse=True)
        head = ([col("region_code", "Code"), col("region_name", "Region"), col("area_name", "Area")]
                if dim == "region" else
                [col("branch_code", "Code"), col("branch_name", "Branch"),
                 col("region_name", "Region"), col("city", "City")])
        columns = head + [col("accounts", "Accounts", "int"),
                          col("balance", "Balance (EUR)", "eur"),
                          col("prev_balance", "Prev month (EUR)", "eur"),
                          col("change", "Change (EUR)", "eur"),
                          col("change_pct", "Change %", "pct")]
        return table(columns, data)

    return build


def make_movement_monthly() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, si, ni, li, ei = (cols.index("movement_month"), cols.index("customers_start"),
                              cols.index("customers_new"), cols.index("customers_lost"),
                              cols.index("customers_end"))
        pred = lambda r: from_ym <= r[mi][:7] <= to_ym
        agg = aggregate(rows, [mi],
                        [("start", "sum", si), ("new", "sum", ni), ("lost", "sum", li),
                         ("end", "sum", ei)], pred, key_fn=lambda r: (r[mi][:7],))
        data: list[list[Any]] = []
        by_month = {ar["__key"][0]: ar for ar in agg}
        for ym in month_sequence(from_ym, to_ym):
            ar = by_month.get(ym, {})
            start = ar.get("start", 0)
            new = ar.get("new", 0)
            lost = ar.get("lost", 0)
            end = ar.get("end", 0)
            data.append([ym, month_label(ym), start, new, lost, end,
                         round(new / start * 100, 2) if start else None,
                         round(lost / start * 100, 2) if start else None,
                         round((end - new) / start * 100, 2) if start else None])
        columns = [col("ym", "Month", "str"), col("month", "Month", "str"),
                   col("start", "Clients at start", "int"),
                   col("new", "Acquired", "int"),
                   col("lost", "Churned", "int"),
                   col("end", "Clients at end", "int"),
                   col("acquisition_pct", "Acquisition %", "pct"),
                   col("churn_pct", "Churn %", "pct"),
                   col("retention_pct", "Retention %", "pct")]
        return table(columns, data)

    return build


def make_movement_analytic() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str,
                    dim: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, bi, si, ni, li, ei = (cols.index("movement_month"), cols.index("branch_id"),
                                  cols.index("customers_start"), cols.index("customers_new"),
                                  cols.index("customers_lost"), cols.index("customers_end"))
        by_branch: dict[int, list[Any]] = {}
        for r in rows:
            ym = r[mi][:7]
            if not (from_ym <= ym <= to_ym):
                continue
            d = by_branch.setdefault(r[bi], [None, 0, 0, None])
            if ym == from_ym:
                d[0] = r[si]
            d[1] += r[ni]
            d[2] += r[li]
            if ym == to_ym:
                d[3] = r[ei]
        data: list[list[Any]] = []
        for bid, (start, new, lost, end) in by_branch.items():
            b = maps["branches"].get(bid, {})
            r = maps["regions"].get(b.get("region_id"), {})
            row = ([r.get("region_code"), r.get("region_name"), r.get("area_name")]
                   if dim == "region" else
                   [b.get("branch_code"), b.get("branch_name"),
                    r.get("region_name"), b.get("city")])
            net = new - lost
            retention = round((end - new) / start * 100, 2) if start else None
            data.append(row + [start, new, lost, end, net, retention])
        if dim == "region":
            merged: dict[str, list[Any]] = {}
            for row in data:
                k = row[0]
                if k not in merged:
                    merged[k] = [row[0], row[1], row[2], 0, 0, 0, 0, 0, 0.0]
                m = merged[k]
                m[3] += row[3]
                m[4] += row[4]
                m[5] += row[5]
                m[6] += row[6]
                m[7] += row[7]
            data = []
            for m in merged.values():
                retention = round((m[6] - m[4]) / m[3] * 100, 2) if m[3] else None
                data.append(m[:8] + [retention])
        data.sort(key=lambda row: row[4], reverse=True)
        head = ([col("region_code", "Code"), col("region_name", "Region"), col("area_name", "Area")]
                if dim == "region" else
                [col("branch_code", "Code"), col("branch_name", "Branch"),
                 col("region_name", "Region"), col("city", "City")])
        columns = head + [col("start", "Start", "int"), col("new", "Acquired", "int"),
                          col("lost", "Churned", "int"), col("end", "End", "int"),
                          col("net", "Net", "int"), col("retention_pct", "Retention %", "pct")]
        return table(columns, data)

    return build


def make_penetration_monthly() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, ei, ri, c2, c3, c4 = (cols.index("movement_month"), cols.index("customers_end"),
                                  cols.index("product_relations"), cols.index("customers_2plus"),
                                  cols.index("customers_3plus"), cols.index("customers_4plus"))
        pred = lambda r: from_ym <= r[mi][:7] <= to_ym
        agg = aggregate(rows, [mi],
                        [("end", "sum", ei), ("rel", "sum", ri),
                         ("c2", "sum", c2), ("c3", "sum", c3), ("c4", "sum", c4)],
                        pred, key_fn=lambda r: (r[mi][:7],))
        data: list[list[Any]] = []
        by_month = {ar["__key"][0]: ar for ar in agg}
        for ym in month_sequence(from_ym, to_ym):
            ar = by_month.get(ym, {})
            end = ar.get("end", 0)
            data.append([ym, month_label(ym), end,
                         round(ar.get("rel", 0) / end, 2) if end else None,
                         round(ar.get("c2", 0) / end * 100, 2) if end else None,
                         round(ar.get("c3", 0) / end * 100, 2) if end else None,
                         round(ar.get("c4", 0) / end * 100, 2) if end else None])
        columns = [col("ym", "Month", "str"), col("month", "Month", "str"),
                   col("end", "Clients", "int"),
                   col("products_per_client", "Products per client", "dec"),
                   col("pct_2plus", "2+ products %", "pct"),
                   col("pct_3plus", "3+ products %", "pct"),
                   col("pct_4plus", "4+ products %", "pct")]
        return table(columns, data)

    return build


def make_penetration_analytic() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str,
                    dim: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, bi, ei, ri, c2, c3, c4 = (cols.index("movement_month"), cols.index("branch_id"),
                                      cols.index("customers_end"), cols.index("product_relations"),
                                      cols.index("customers_2plus"), cols.index("customers_3plus"),
                                      cols.index("customers_4plus"))
        # use the latest available month within the period
        latest = max((r[mi][:7] for r in rows if from_ym <= r[mi][:7] <= to_ym), default=None)
        if latest is None:
            return table([], [])
        by_branch: dict[int, list[Any]] = {}
        for r in rows:
            if r[mi][:7] != latest:
                continue
            by_branch[r[bi]] = [r[ei], r[ri], r[c2], r[c3], r[c4]]
        # raw values per entity; ratios are derived after possible region merge
        data: list[list[Any]] = []
        for bid, (end, rel, c2, c3, c4) in by_branch.items():
            b = maps["branches"].get(bid, {})
            r = maps["regions"].get(b.get("region_id"), {})
            row = ([r.get("region_code"), r.get("region_name"), r.get("area_name")]
                   if dim == "region" else
                   [b.get("branch_code"), b.get("branch_name"),
                    r.get("region_name"), b.get("city")])
            data.append(row + [end, rel, c2, c3, c4])
        if dim == "region":
            merged: dict[str, list[Any]] = {}
            for row in data:
                k = row[0]
                if k not in merged:
                    merged[k] = [row[0], row[1], row[2], 0, 0, 0, 0, 0]
                m = merged[k]
                for i in range(3, 8):
                    m[i] += row[i]
            data = []
            for m in merged.values():
                end = m[3]
                data.append([m[0], m[1], m[2], end,
                             round(m[4] / end, 2) if end else None,
                             round(m[5] / end * 100, 2) if end else None,
                             round(m[6] / end * 100, 2) if end else None,
                             round(m[7] / end * 100, 2) if end else None])
        else:
            # branch rows: [code, name, region, city, end, rel, c2, c3, c4]
            data = [[row[0], row[1], row[2], row[3], row[4],
                     round(row[5] / row[4], 2) if row[4] else None,
                     round(row[6] / row[4] * 100, 2) if row[4] else None,
                     round(row[7] / row[4] * 100, 2) if row[4] else None,
                     round(row[8] / row[4] * 100, 2) if row[4] else None]
                    for row in data]
        # sort by Clients (end): region rows keep it at index 3, branch rows at 4
        sort_idx = 3 if dim == "region" else 4
        data.sort(key=lambda row: row[sort_idx] or 0, reverse=True)
        head = ([col("region_code", "Code"), col("region_name", "Region"), col("area_name", "Area")]
                if dim == "region" else
                [col("branch_code", "Code"), col("branch_name", "Branch"),
                 col("region_name", "Region"), col("city", "City")])
        columns = head + [col("end", "Clients", "int"),
                          col("products_per_client", "Products per client", "dec"),
                          col("pct_2plus", "2+ products %", "pct"),
                          col("pct_3plus", "3+ products %", "pct"),
                          col("pct_4plus", "4+ products %", "pct")]
        return table(columns, data)

    return build


def make_campaigns_monthly() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        ccols, crows = await cache.get_table("MIS_DIM_CAMPAIGN")
        rcols, rrows = await cache.get_table("MIS_FACT_CAMPAIGN_RESULTS")
        cid_i = rcols.index("campaign_id")
        end_by_id = {c[cid_i]: c[ccols.index("end_date")][:7] for c in crows}
        pred = lambda r: from_ym <= end_by_id[r[cid_i]] <= to_ym
        agg = aggregate(rrows, [cid_i],
                        [("contacts", "sum", rcols.index("contacts")),
                         ("responses", "sum", rcols.index("responses")),
                         ("conversions", "sum", rcols.index("conversions")),
                         ("sales", "sum", rcols.index("sales_amount")),
                         ("cost", "sum", rcols.index("cost"))], pred)
        # group the campaign-level rows by end month
        by_month: dict[str, list[Any]] = {}
        for ar in agg:
            ym = end_by_id[ar["__key"][0]]
            d = by_month.setdefault(ym, [0, 0, 0, 0, 0.0, 0.0])
            d[0] += 1
            d[1] += ar["contacts"]
            d[2] += ar["responses"]
            d[3] += ar["conversions"]
            d[4] += ar["sales"]
            d[5] += ar["cost"]
        data: list[list[Any]] = []
        for ym in month_sequence(from_ym, to_ym):
            campaigns, contacts, responses, conversions, sales, cost = by_month.get(
                ym, [0, 0, 0, 0, 0.0, 0.0])
            data.append([ym, month_label(ym), campaigns, contacts, responses, conversions,
                         round(responses / contacts * 100, 2) if contacts else None,
                         round(conversions / responses * 100, 2) if responses else None,
                         round(cost, 2), round(sales, 2),
                         round(cost / conversions, 2) if conversions else None,
                         round((sales - cost) / cost * 100, 2) if cost else None])
        columns = [col("ym", "Month", "str"), col("month", "Month", "str"),
                   col("campaigns", "Campaigns", "int"),
                   col("contacts", "Contacts", "int"),
                   col("responses", "Responses", "int"),
                   col("conversions", "Conversions", "int"),
                   col("response_pct", "Response %", "pct"),
                   col("conversion_pct", "Conversion %", "pct"),
                   col("cost", "Cost (EUR)", "eur"),
                   col("sales_value", "Sales value (EUR)", "eur"),
                   col("cost_per_conversion", "Cost / conversion (EUR)", "eur"),
                   col("roi_pct", "ROI %", "pct")]
        return table(columns, data)

    return build


def make_campaigns_analytic() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str,
                    dim: str) -> dict[str, Any]:
        ccols, crows = await cache.get_table("MIS_DIM_CAMPAIGN")
        rcols, rrows = await cache.get_table("MIS_FACT_CAMPAIGN_RESULTS")
        bcols, brows = await cache.get_table("MIS_DIM_BRANCH")
        pcols, prows = await cache.get_table("MIS_DIM_PRODUCT")
        cid_i = rcols.index("campaign_id")
        end_by_id = {c[cid_i]: c[ccols.index("end_date")][:7] for c in crows}
        pred = lambda r: from_ym <= end_by_id[r[cid_i]] <= to_ym

        if dim == "campaign":
            key_idx = cid_i
        elif dim == "branch":
            key_idx = rcols.index("branch_id")
        else:  # campaign type
            key_idx = cid_i
        agg = aggregate(rrows, [key_idx],
                        [("contacts", "sum", rcols.index("contacts")),
                         ("responses", "sum", rcols.index("responses")),
                         ("conversions", "sum", rcols.index("conversions")),
                         ("sales", "sum", rcols.index("sales_amount")),
                         ("cost", "sum", rcols.index("cost"))], pred)
        if dim == "campaign":
            agg.sort(key=lambda ar: ar.get("sales") or 0, reverse=True)
            data: list[list[Any]] = []
            for ar in agg:
                c = maps["campaigns"][ar["__key"][0]]
                p = maps["products"].get(c.get("product_id"), {})
                contacts, responses, conversions = ar["contacts"], ar["responses"], ar["conversions"]
                sales, cost = ar["sales"], ar["cost"]
                data.append([c.get("campaign_code"), c.get("campaign_name"),
                             c.get("campaign_type"), c.get("target_segment"),
                             p.get("product_name"), contacts, responses, conversions,
                             round(responses / contacts * 100, 2) if contacts else None,
                             round(conversions / responses * 100, 2) if responses else None,
                             round(cost, 2), round(sales, 2),
                             round(cost / conversions, 2) if conversions else None,
                             round((sales - cost) / cost * 100, 2) if cost else None])
            columns = [col("campaign_code", "Code"), col("campaign_name", "Campaign"),
                       col("campaign_type", "Type"), col("target_segment", "Segment"),
                       col("product_name", "Product"),
                       col("contacts", "Contacts", "int"),
                       col("responses", "Responses", "int"),
                       col("conversions", "Conversions", "int"),
                       col("response_pct", "Response %", "pct"),
                       col("conversion_pct", "Conversion %", "pct"),
                       col("cost", "Cost (EUR)", "eur"),
                       col("sales_value", "Sales value (EUR)", "eur"),
                       col("cost_per_conversion", "Cost / conv. (EUR)", "eur"),
                       col("roi_pct", "ROI %", "pct")]
            return table(columns, data)

        if dim == "type":
            type_by_id = {c[cid_i]: c[ccols.index("campaign_type")] for c in crows}
            by_type: dict[str, list[Any]] = {}
            for ar in agg:
                t = type_by_id[ar["__key"][0]]
                d = by_type.setdefault(t, [0, 0, 0, 0, 0.0, 0.0])
                d[0] += 1
                d[1] += ar["contacts"]
                d[2] += ar["responses"]
                d[3] += ar["conversions"]
                d[4] += ar["sales"]
                d[5] += ar["cost"]
            data = []
            for t, (n, contacts, responses, conversions, sales, cost) in by_type.items():
                data.append([t, n, contacts, responses, conversions,
                             round(responses / contacts * 100, 2) if contacts else None,
                             round(conversions / responses * 100, 2) if responses else None,
                             round(cost, 2), round(sales, 2),
                             round((sales - cost) / cost * 100, 2) if cost else None])
            data.sort(key=lambda row: row[8] or 0, reverse=True)
            columns = [col("campaign_type", "Campaign type"),
                       col("campaigns", "Campaigns", "int"),
                       col("contacts", "Contacts", "int"),
                       col("responses", "Responses", "int"),
                       col("conversions", "Conversions", "int"),
                       col("response_pct", "Response %", "pct"),
                       col("conversion_pct", "Conversion %", "pct"),
                       col("cost", "Cost (EUR)", "eur"),
                       col("sales_value", "Sales value (EUR)", "eur"),
                       col("roi_pct", "ROI %", "pct")]
            return table(columns, data)

        # by branch
        agg.sort(key=lambda ar: ar.get("sales") or 0, reverse=True)
        data = []
        for ar in agg:
            b = maps["branches"].get(ar["__key"][0], {})
            r = maps["regions"].get(b.get("region_id"), {})
            contacts, responses, conversions = ar["contacts"], ar["responses"], ar["conversions"]
            sales, cost = ar["sales"], ar["cost"]
            data.append([b.get("branch_code"), b.get("branch_name"), r.get("region_name"),
                         contacts, responses, conversions,
                         round(responses / contacts * 100, 2) if contacts else None,
                         round(conversions / responses * 100, 2) if responses else None,
                         round(cost, 2), round(sales, 2),
                         round((sales - cost) / cost * 100, 2) if cost else None])
        columns = [col("branch_code", "Code"), col("branch_name", "Branch"),
                   col("region_name", "Region"),
                   col("contacts", "Contacts", "int"),
                   col("responses", "Responses", "int"),
                   col("conversions", "Conversions", "int"),
                   col("response_pct", "Response %", "pct"),
                   col("conversion_pct", "Conversion %", "pct"),
                   col("cost", "Cost (EUR)", "eur"),
                   col("sales_value", "Sales value (EUR)", "eur"),
                   col("roi_pct", "ROI %", "pct")]
        return table(columns, data)

    return build


# ---------------------------------------------------------------------------
# Chart builders
# ---------------------------------------------------------------------------

async def chart_sales_vs_plan(cache: Any, maps: dict[str, Any], from_ym: str,
                              to_ym: str) -> dict[str, Any]:
    """Overview trend with a network plan series when plans are available."""
    cols, rows = await cache.get_table("MIS_FACT_SALES")
    sales = aggregate(filtered_sales(cols, rows, maps, from_ym, to_ym),
                      [cols.index("sale_date")],
                      [("amount", "sum", cols.index("amount"))])
    by_sales = {r["__key"][0]: r.get("amount", 0) for r in sales}
    by_plan: dict[str, float] = {}
    try:
        pcols, prows = await cache.get_table("MIS_FACT_BRANCH_PLAN")
        mi, ai = pcols.index("plan_month"), pcols.index("plan_amount")
        for row in prows:
            ym = row[mi][:7]
            if from_ym <= ym <= to_ym:
                by_plan[ym] = by_plan.get(ym, 0.0) + row[ai]
    except KeyError:
        pass
    months = month_sequence(from_ym, to_ym)
    series_list = [series("Actual (EUR)", [round(by_sales.get(m, 0), 2) for m in months])]
    if by_plan:
        series_list.append(series("Plan (EUR)", [round(by_plan.get(m, 0), 2) for m in months]))
    return chart("sales_plan", "line", "Sales vs plan", [month_label(m) for m in months], series_list)

def chart_sales_trend(group: str | None = None) -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_SALES")
        agg = aggregate(filtered_sales(cols, rows, maps, from_ym, to_ym, group),
                        [cols.index("sale_date")],
                        [("amount", "sum", cols.index("amount"))])
        by_month = {r["__key"][0]: r.get("amount", 0) for r in agg}
        months = month_sequence(from_ym, to_ym)
        labels = [month_label(ym) for ym in months]
        values = [round(by_month.get(ym, 0), 2) for ym in months]
        return chart("trend", "line", "Sales by month", labels, [series("Amount (EUR)", values)])
    return build


def chart_group_share() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_SALES")
        pi, si, di = cols.index("product_id"), cols.index("sale_status"), cols.index("sale_date")
        agg = aggregate(filtered_sales(cols, rows, maps, from_ym, to_ym),
                        [pi], [("amount", "sum", cols.index("amount"))])
        by_group: dict[str, float] = {}
        for ar in agg:
            g = maps["products"][ar["__key"][0]]["product_group"]
            by_group[g] = by_group.get(g, 0) + ar["amount"]
        items = sorted(by_group.items(), key=lambda kv: kv[1], reverse=True)
        return chart("group_share", "doughnut", "Sales share by product group",
                     [k for k, _ in items], [series("Amount (EUR)", [round(v, 2) for _, v in items])])
    return build


def chart_share_by(dim: str, title: str, group: str | None = None) -> Callable:
    """Doughnut: share of sales for a product group by one dimension."""
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_SALES")
        key_idx = {"product": cols.index("product_id"), "region": cols.index("branch_id"),
                   "channel": cols.index("channel_id")}[dim]
        agg = aggregate(filtered_sales(cols, rows, maps, from_ym, to_ym, group),
                        [key_idx], [("amount", "sum", cols.index("amount"))])
        if dim == "region":
            pairs: dict[str, float] = {}
            for ar in agg:
                rid = maps["branches"][ar["__key"][0]]["region_id"]
                name = maps["regions"][rid]["region_name"]
                pairs[name] = pairs.get(name, 0) + ar["amount"]
        elif dim == "product":
            pairs = {maps["products"][ar["__key"][0]]["product_subgroup"]: ar["amount"] for ar in agg}
        else:
            pairs = {maps["channels"][ar["__key"][0]]["channel_name"]: ar["amount"] for ar in agg}
        items = sorted(pairs.items(), key=lambda kv: kv[1], reverse=True)
        return chart("share", "doughnut", title,
                     [k for k, _ in items], [series("Amount (EUR)", [round(v, 2) for _, v in items])])
    return build


def chart_region_bar(title: str, group: str | None = None) -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_SALES")
        agg = aggregate(filtered_sales(cols, rows, maps, from_ym, to_ym, group),
                        [cols.index("branch_id")],
                        [("amount", "sum", cols.index("amount"))])
        by_region: dict[str, float] = {}
        for ar in agg:
            rid = maps["branches"][ar["__key"][0]]["region_id"]
            name = maps["regions"][rid]["region_name"]
            by_region[name] = by_region.get(name, 0) + ar["amount"]
        items = sorted(by_region.items(), key=lambda kv: kv[1], reverse=True)
        return chart("by_region", "bar", title,
                     [k for k, _ in items], [series("Amount (EUR)", [round(v, 2) for _, v in items])])
    return build


def chart_top_entities(dim: str, title: str, group: str | None = None, top: int = 10) -> Callable:
    """Horizontal bar: top-N advisors or branches by sales value."""
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_SALES")
        key_idx = {"advisor": cols.index("advisor_id"), "branch": cols.index("branch_id")}[dim]
        agg = aggregate(filtered_sales(cols, rows, maps, from_ym, to_ym, group),
                        [key_idx], [("amount", "sum", cols.index("amount"))])
        agg.sort(key=lambda ar: ar.get("amount") or 0, reverse=True)
        agg = agg[:top]
        labels, values = [], []
        for ar in agg:
            if dim == "advisor":
                a = maps["advisors"].get(ar["__key"][0], {})
                labels.append(f"{a.get('first_name')} {a.get('last_name')}")
            else:
                b = maps["branches"].get(ar["__key"][0], {})
                labels.append(b.get("branch_name"))
            values.append(round(ar["amount"], 2))
        labels.reverse()
        values.reverse()
        return chart("top", "hbar", title, labels, [series("Amount (EUR)", values)])
    return build


def chart_balances_trend(groups: list[str]) -> Callable:
    """Line chart of total balances by month, one series per product group."""
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_BALANCES")
        mi, pi, ai = cols.index("balance_month"), cols.index("product_id"), cols.index("balance_amount")
        pred = lambda r: (from_ym <= r[mi][:7] <= to_ym
                          and maps["products"][r[pi]]["product_group"] in groups)
        agg = aggregate(rows, [mi, pi], [("balance", "sum", ai)], pred,
                        key_fn=lambda r: (r[mi][:7], r[pi]))
        labels = month_sequence(from_ym, to_ym)
        labels_txt = [month_label(l) for l in labels]
        series_list = []
        for g in groups:
            data = [round(next((r["balance"] for r in agg
                                if r["__key"][0] == l and maps["products"][r["__key"][1]]["product_group"] == g),
                               0), 2)
                    for l in labels]
            series_list.append(series(g, data))
        return chart("balances_trend", "line", "Balances by month", labels_txt, series_list)
    return build


def chart_acquisition_churn() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, ni, li = cols.index("movement_month"), cols.index("customers_new"), cols.index("customers_lost")
        pred = lambda r: from_ym <= r[mi][:7] <= to_ym
        agg = aggregate(rows, [mi], [("new", "sum", ni), ("lost", "sum", li)],
                        pred, key_fn=lambda r: (r[mi][:7],))
        by_month = {r["__key"][0]: r for r in agg}
        months = month_sequence(from_ym, to_ym)
        labels = [month_label(ym) for ym in months]
        return chart("acquisition_churn", "bar", "Client acquisition vs churn",
                     labels, [series("Acquired", [by_month.get(ym, {}).get("new", 0)
                                                   for ym in months]),
                              series("Churned", [by_month.get(ym, {}).get("lost", 0)
                                                 for ym in months])])
    return build


def chart_penetration_trend() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, ei, ri = cols.index("movement_month"), cols.index("customers_end"), cols.index("product_relations")
        pred = lambda r: from_ym <= r[mi][:7] <= to_ym
        agg = aggregate(rows, [mi], [("end", "sum", ei), ("rel", "sum", ri)],
                        pred, key_fn=lambda r: (r[mi][:7],))
        by_month = {r["__key"][0]: r for r in agg}
        months = month_sequence(from_ym, to_ym)
        labels = [month_label(ym) for ym in months]
        values = [round(by_month.get(ym, {}).get("rel", 0)
                        / by_month.get(ym, {}).get("end", 0), 2)
                  if by_month.get(ym, {}).get("end") else None for ym in months]
        return chart("penetration", "line", "Products per client by month",
                     labels, [series("Products per client", values)])
    return build


def chart_penetration_distribution() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, ei, c2, c3, c4 = (cols.index("movement_month"), cols.index("customers_end"),
                              cols.index("customers_2plus"), cols.index("customers_3plus"),
                              cols.index("customers_4plus"))
        latest = max((r[mi][:7] for r in rows if from_ym <= r[mi][:7] <= to_ym), default=None)
        if latest is None:
            return chart("distribution", "bar", "Product holdings per client", [], [])
        end = c2p = c3p = c4p = 0
        for r in rows:
            if r[mi][:7] != latest:
                continue
            end += r[ei]
            c2p += r[c2]
            c3p += r[c3]
            c4p += r[c4]
        p2 = c2p / end * 100 if end else 0
        p3 = c3p / end * 100 if end else 0
        p4 = c4p / end * 100 if end else 0
        labels = ["1 product", "2 products", "3 products", "4+ products"]
        values = [round(100 - p2, 2), round(p2 - p3, 2), round(p3 - p4, 2), round(p4, 2)]
        return chart("distribution", "bar", f"Product holdings per client ({month_label(latest)})",
                     labels, [series("Clients %", values)])
    return build


def chart_campaign_roi(top: int = 10) -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        ccols, crows = await cache.get_table("MIS_DIM_CAMPAIGN")
        rcols, rrows = await cache.get_table("MIS_FACT_CAMPAIGN_RESULTS")
        cid_i = rcols.index("campaign_id")
        end_by_id = {c[cid_i]: c[ccols.index("end_date")][:7] for c in crows}
        pred = lambda r: from_ym <= end_by_id[r[cid_i]] <= to_ym
        agg = aggregate(rrows, [cid_i],
                        [("sales", "sum", rcols.index("sales_amount")),
                         ("cost", "sum", rcols.index("cost"))], pred)
        items = []
        for ar in agg:
            c = maps["campaigns"][ar["__key"][0]]
            roi = round((ar["sales"] - ar["cost"]) / ar["cost"] * 100, 2) if ar["cost"] else 0
            items.append((c.get("campaign_name"), roi))
        items.sort(key=lambda kv: kv[1], reverse=True)
        items = items[:top][::-1]
        return chart("roi", "hbar", f"Top {top} campaigns by ROI",
                     [k for k, _ in items], [series("ROI %", [v for _, v in items])])
    return build


def chart_campaign_type_share() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        ccols, crows = await cache.get_table("MIS_DIM_CAMPAIGN")
        rcols, rrows = await cache.get_table("MIS_FACT_CAMPAIGN_RESULTS")
        cid_i = rcols.index("campaign_id")
        end_by_id = {c[cid_i]: c[ccols.index("end_date")][:7] for c in crows}
        pred = lambda r: from_ym <= end_by_id[r[cid_i]] <= to_ym
        agg = aggregate(rrows, [cid_i], [("conversions", "sum", rcols.index("conversions"))], pred)
        by_type: dict[str, int] = {}
        for ar in agg:
            t = maps["campaigns"][ar["__key"][0]]["campaign_type"]
            by_type[t] = by_type.get(t, 0) + ar["conversions"]
        items = sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)
        return chart("type_share", "doughnut", "Conversions by campaign type",
                     [k for k, _ in items], [series("Conversions", [v for _, v in items])])
    return build


def chart_campaign_funnel() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        ccols, crows = await cache.get_table("MIS_DIM_CAMPAIGN")
        rcols, rrows = await cache.get_table("MIS_FACT_CAMPAIGN_RESULTS")
        cid_i = rcols.index("campaign_id")
        end_by_id = {c[cid_i]: c[ccols.index("end_date")][:7] for c in crows}
        pred = lambda r: from_ym <= end_by_id[r[cid_i]] <= to_ym
        agg = aggregate(rrows, [cid_i],
                        [("contacts", "sum", rcols.index("contacts")),
                         ("responses", "sum", rcols.index("responses")),
                         ("conversions", "sum", rcols.index("conversions"))], pred)
        by_month: dict[str, list[int]] = {}
        for ar in agg:
            ym = end_by_id[ar["__key"][0]]
            d = by_month.setdefault(ym, [0, 0, 0])
            d[0] += ar["contacts"]
            d[1] += ar["responses"]
            d[2] += ar["conversions"]
        months = month_sequence(from_ym, to_ym)
        labels = [month_label(ym) for ym in months]
        return chart("funnel", "line", "Campaign funnel by month",
                     labels, [series("Contacts", [by_month.get(ym, [0, 0, 0])[0]
                                                   for ym in months]),
                              series("Responses", [by_month.get(ym, [0, 0, 0])[1]
                                                   for ym in months]),
                              series("Conversions", [by_month.get(ym, [0, 0, 0])[2]
                                                     for ym in months])])
    return build


def chart_balance_region_share(groups: list[str], title: str) -> Callable:
    """Doughnut: share of balances by region at period end."""
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_BALANCES")
        mi, bi, pi, ai = cols.index("balance_month"), cols.index("branch_id"), \
            cols.index("product_id"), cols.index("balance_amount")
        latest = max((r[mi][:7] for r in rows if from_ym <= r[mi][:7] <= to_ym
                      and maps["products"][r[pi]]["product_group"] in groups), default=None)
        if latest is None:
            return chart("region_share", "doughnut", title, [], [])
        by_region: dict[str, float] = {}
        for r in rows:
            if r[mi][:7] != latest or maps["products"][r[pi]]["product_group"] not in groups:
                continue
            rid = maps["branches"][r[bi]]["region_id"]
            name = maps["regions"][rid]["region_name"]
            by_region[name] = by_region.get(name, 0) + r[ai]
        items = sorted(by_region.items(), key=lambda kv: kv[1], reverse=True)
        return chart("region_share", "doughnut", title,
                     [k for k, _ in items], [series("Balance (EUR)", [round(v, 2) for _, v in items])])
    return build


def chart_avg_balance_by_branch(groups: list[str], title: str, top: int = 12) -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_BALANCES")
        mi, bi, pi, ci, ai = cols.index("balance_month"), cols.index("branch_id"), \
            cols.index("product_id"), cols.index("account_count"), cols.index("balance_amount")
        latest = max((r[mi][:7] for r in rows if from_ym <= r[mi][:7] <= to_ym
                      and maps["products"][r[pi]]["product_group"] in groups), default=None)
        if latest is None:
            return chart("avg_branch", "hbar", title, [], [])
        by_branch: dict[int, list[float]] = {}
        for r in rows:
            if r[mi][:7] != latest or maps["products"][r[pi]]["product_group"] not in groups:
                continue
            d = by_branch.setdefault(r[bi], [0, 0.0])
            d[0] += r[ci]
            d[1] += r[ai]
        items = [(maps["branches"][bid]["branch_name"], d[1] / d[0] if d[0] else 0)
                 for bid, d in by_branch.items()]
        items.sort(key=lambda kv: kv[1], reverse=True)
        items = items[:top][::-1]
        return chart("avg_branch", "hbar", title,
                     [k for k, _ in items], [series("Avg balance (EUR)", [round(v, 2) for _, v in items])])
    return build


def chart_top_branches_by_acquisition(top: int = 10) -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, bi, ni = cols.index("movement_month"), cols.index("branch_id"), cols.index("customers_new")
        pred = lambda r: from_ym <= r[mi][:7] <= to_ym
        agg = aggregate(rows, [bi], [("new", "sum", ni)], pred)
        agg.sort(key=lambda ar: ar.get("new") or 0, reverse=True)
        agg = agg[:top][::-1]
        labels = [maps["branches"][ar["__key"][0]]["branch_name"] for ar in agg]
        values = [ar["new"] for ar in agg]
        return chart("top_acquisition", "hbar", f"Top {top} branches by acquired clients",
                     labels, [series("New clients", values)])
    return build


def chart_client_base_trend() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, ei = cols.index("movement_month"), cols.index("customers_end")
        pred = lambda r: from_ym <= r[mi][:7] <= to_ym
        agg = aggregate(rows, [mi], [("end", "sum", ei)], pred,
                        key_fn=lambda r: (r[mi][:7],))
        agg.sort(key=lambda r: r["__key"][0])
        labels = [month_label(r["__key"][0]) for r in agg]
        return chart("base", "line", "Client base by month",
                     labels, [series("Clients", [r["end"] for r in agg])])
    return build


def chart_cross_sell_by_region() -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, bi, ei, c2 = (cols.index("movement_month"), cols.index("branch_id"),
                          cols.index("customers_end"), cols.index("customers_2plus"))
        pred = lambda r: from_ym <= r[mi][:7] <= to_ym
        agg = aggregate(rows, [bi], [("end", "sum", ei), ("c2", "sum", c2)], pred)
        by_region: dict[str, float] = {}
        for ar in agg:
            rid = maps["branches"][ar["__key"][0]]["region_id"]
            name = maps["regions"][rid]["region_name"]
            d = by_region.setdefault(name, [0, 0])
            d[0] += ar["end"]
            d[1] += ar["c2"]
        items = sorted(by_region.items(), key=lambda kv: kv[1][1] / kv[1][0] if kv[1][0] else 0,
                       reverse=True)
        return chart("cross_sell", "bar", "Cross-sell ratio (2+ products) by region",
                     [k for k, _ in items],
                     [series("Clients with 2+ products %",
                             [round(v[1] / v[0] * 100, 2) if v[0] else 0 for _, v in items])])
    return build


# ---------------------------------------------------------------------------
# KPI builders
# ---------------------------------------------------------------------------

def kpi_sales_period(group: str | None = None, key: str = "value", label: str = "Sales",
                     fmt: str = "eur") -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        cols, rows = await cache.get_table("MIS_FACT_SALES")
        total = 0.0
        for r in filtered_sales(cols, rows, maps, from_ym, to_ym, group):
            total += r[cols.index("amount")]
        return [{"key": key, "label": label, "value": round(total, 2), "fmt": fmt}]
    return build


def kpi_sales_average(group: str | None = None, key: str = "value",
                      label: str = "Average value", fmt: str = "eur") -> Callable:
    """Average booked sale amount, with the denominator matching the filter."""
    async def build(cache: Any, maps: dict[str, Any], from_ym: str,
                    to_ym: str) -> list[dict[str, Any]]:
        cols, rows = await cache.get_table("MIS_FACT_SALES")
        amount_i = cols.index("amount")
        values = [r[amount_i] for r in filtered_sales(cols, rows, maps, from_ym,
                                                       to_ym, group)
                  if r[amount_i] is not None]
        value = round(sum(values) / len(values), 2) if values else None
        return [{"key": key, "label": label, "value": value, "fmt": fmt}]
    return build


def kpi_sales_commission(group: str | None = None, key: str = "value",
                         label: str = "Commission", fmt: str = "eur") -> Callable:
    """Sum the commission column rather than accidentally summing amount."""
    async def build(cache: Any, maps: dict[str, Any], from_ym: str,
                    to_ym: str) -> list[dict[str, Any]]:
        cols, rows = await cache.get_table("MIS_FACT_SALES")
        commission_i = cols.index("commission")
        total = sum((r[commission_i] or 0.0)
                    for r in filtered_sales(cols, rows, maps, from_ym, to_ym, group))
        return [{"key": key, "label": label, "value": round(total, 2), "fmt": fmt}]
    return build


def kpi_sales_count(group: str | None = None, key: str = "value", label: str = "Units sold",
                    fmt: str = "int") -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        cols, rows = await cache.get_table("MIS_FACT_SALES")
        n = sum(1 for _ in filtered_sales(cols, rows, maps, from_ym, to_ym, group))
        return [{"key": key, "label": label, "value": n, "fmt": fmt}]
    return build


def kpi_balances(groups: list[str], key: str = "value", label: str = "Balance",
                 fmt: str = "eur") -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        cols, rows = await cache.get_table("MIS_FACT_BALANCES")
        mi, pi, ai = cols.index("balance_month"), cols.index("product_id"), cols.index("balance_amount")
        latest = max((r[mi][:7] for r in rows if from_ym <= r[mi][:7] <= to_ym
                      and maps["products"][r[pi]]["product_group"] in groups), default=None)
        if latest is None:
            return [{"key": key, "label": label, "value": None, "fmt": fmt}]
        total = sum(r[ai] for r in rows
                    if r[mi][:7] == latest and maps["products"][r[pi]]["product_group"] in groups)
        return [{"key": key, "label": label, "value": round(total, 2), "fmt": fmt}]
    return build


def kpi_balances_count(groups: list[str], key: str = "value", label: str = "Accounts",
                       fmt: str = "int") -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        cols, rows = await cache.get_table("MIS_FACT_BALANCES")
        mi, pi, ci = cols.index("balance_month"), cols.index("product_id"), cols.index("account_count")
        latest = max((r[mi][:7] for r in rows if from_ym <= r[mi][:7] <= to_ym
                      and maps["products"][r[pi]]["product_group"] in groups), default=None)
        if latest is None:
            return [{"key": key, "label": label, "value": None, "fmt": fmt}]
        total = sum(r[ci] for r in rows
                    if r[mi][:7] == latest and maps["products"][r[pi]]["product_group"] in groups)
        return [{"key": key, "label": label, "value": total, "fmt": fmt}]
    return build


def kpi_movement(measure: str, key: str = "value", label: str = "", fmt: str = "int") -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi = cols.index("movement_month")
        idx = cols.index(measure)
        if measure == "customers_end":
            latest = max((r[mi][:7] for r in rows if from_ym <= r[mi][:7] <= to_ym), default=None)
            total = sum(r[idx] for r in rows if r[mi][:7] == latest)
        else:
            total = sum(r[idx] for r in rows if from_ym <= r[mi][:7] <= to_ym)
        return [{"key": key, "label": label, "value": total, "fmt": fmt}]
    return build


def kpi_retention(key: str = "value", label: str = "Retention %", fmt: str = "pct") -> Callable:
    """Retention in the latest available month: 100 * (end - new) / start."""
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, si, ni, ei = (cols.index("movement_month"), cols.index("customers_start"),
                          cols.index("customers_new"), cols.index("customers_end"))
        latest = max((r[mi][:7] for r in rows if from_ym <= r[mi][:7] <= to_ym), default=None)
        if latest is None:
            return [{"key": key, "label": label, "value": None, "fmt": fmt}]
        start = sum(r[si] for r in rows if r[mi][:7] == latest)
        new = sum(r[ni] for r in rows if r[mi][:7] == latest)
        end = sum(r[ei] for r in rows if r[mi][:7] == latest)
        value = round((end - new) / start * 100, 2) if start else None
        return [{"key": key, "label": label, "value": value, "fmt": fmt}]
    return build


def kpi_penetration(key: str = "value", label: str = "Products per client",
                    fmt: str = "dec") -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, ei, ri = cols.index("movement_month"), cols.index("customers_end"), cols.index("product_relations")
        latest = max((r[mi][:7] for r in rows if from_ym <= r[mi][:7] <= to_ym), default=None)
        if latest is None:
            return [{"key": key, "label": label, "value": None, "fmt": fmt}]
        end = sum(r[ei] for r in rows if r[mi][:7] == latest)
        rel = sum(r[ri] for r in rows if r[mi][:7] == latest)
        return [{"key": key, "label": label, "value": round(rel / end, 2) if end else None, "fmt": fmt}]
    return build


def kpi_penetration_pct(column: str, key: str = "value", label: str = "",
                        fmt: str = "pct") -> Callable:
    """Share of clients with 2+/3+/4+ products in the latest month of the period."""
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        cols, rows = await cache.get_table("MIS_FACT_CUSTOMER_MOVEMENT")
        mi, ei = cols.index("movement_month"), cols.index("customers_end")
        ci = cols.index(column)
        latest = max((r[mi][:7] for r in rows if from_ym <= r[mi][:7] <= to_ym), default=None)
        if latest is None:
            return [{"key": key, "label": label, "value": None, "fmt": fmt}]
        end = sum(r[ei] for r in rows if r[mi][:7] == latest)
        part = sum(r[ci] for r in rows if r[mi][:7] == latest)
        value = round(part / end * 100, 2) if end else None
        return [{"key": key, "label": label, "value": value, "fmt": fmt}]
    return build


def kpi_balances_avg(groups: list[str], key: str = "value", label: str = "Avg balance",
                     fmt: str = "eur") -> Callable:
    """Weighted average balance per account at period end."""
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        cols, rows = await cache.get_table("MIS_FACT_BALANCES")
        mi, pi, ci, ai = (cols.index("balance_month"), cols.index("product_id"),
                          cols.index("account_count"), cols.index("balance_amount"))
        latest = max((r[mi][:7] for r in rows if from_ym <= r[mi][:7] <= to_ym
                      and maps["products"][r[pi]]["product_group"] in groups), default=None)
        if latest is None:
            return [{"key": key, "label": label, "value": None, "fmt": fmt}]
        accounts = sum(r[ci] for r in rows
                       if r[mi][:7] == latest and maps["products"][r[pi]]["product_group"] in groups)
        balance = sum(r[ai] for r in rows
                      if r[mi][:7] == latest and maps["products"][r[pi]]["product_group"] in groups)
        value = round(balance / accounts, 2) if accounts else None
        return [{"key": key, "label": label, "value": value, "fmt": fmt}]
    return build


def kpi_campaigns_roi(key: str = "value", label: str = "Campaign ROI", fmt: str = "pct") -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        ccols, crows = await cache.get_table("MIS_DIM_CAMPAIGN")
        rcols, rrows = await cache.get_table("MIS_FACT_CAMPAIGN_RESULTS")
        cid_i = rcols.index("campaign_id")
        end_by_id = {c[cid_i]: c[ccols.index("end_date")][:7] for c in crows}
        sales = sum(r[rcols.index("sales_amount")] for r in rrows
                    if from_ym <= end_by_id[r[cid_i]] <= to_ym)
        cost = sum(r[rcols.index("cost")] for r in rrows
                   if from_ym <= end_by_id[r[cid_i]] <= to_ym)
        roi = round((sales - cost) / cost * 100, 2) if cost else None
        return [{"key": key, "label": label, "value": roi, "fmt": fmt}]
    return build


def kpi_campaigns_conv(key: str = "value", label: str = "Conversions", fmt: str = "int") -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        ccols, crows = await cache.get_table("MIS_DIM_CAMPAIGN")
        rcols, rrows = await cache.get_table("MIS_FACT_CAMPAIGN_RESULTS")
        cid_i = rcols.index("campaign_id")
        end_by_id = {c[cid_i]: c[ccols.index("end_date")][:7] for c in crows}
        total = sum(r[rcols.index("conversions")] for r in rrows
                    if from_ym <= end_by_id[r[cid_i]] <= to_ym)
        return [{"key": key, "label": label, "value": total, "fmt": fmt}]
    return build


def kpi_campaigns_conversion_rate(key: str = "value", label: str = "Conversion rate",
                                  fmt: str = "pct") -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        ccols, crows = await cache.get_table("MIS_DIM_CAMPAIGN")
        rcols, rrows = await cache.get_table("MIS_FACT_CAMPAIGN_RESULTS")
        cid_i = rcols.index("campaign_id")
        end_by_id = {c[cid_i]: c[ccols.index("end_date")][:7] for c in crows}
        resp = sum(r[rcols.index("responses")] for r in rrows
                   if from_ym <= end_by_id[r[cid_i]] <= to_ym)
        conv = sum(r[rcols.index("conversions")] for r in rrows
                   if from_ym <= end_by_id[r[cid_i]] <= to_ym)
        rate = round(conv / resp * 100, 2) if resp else None
        return [{"key": key, "label": label, "value": rate, "fmt": fmt}]
    return build


async def network_plan(cache: Any, from_ym: str, to_ym: str) -> float | None:
    """Sum branch plans when the optional plan table is available."""
    try:
        cols, rows = await cache.get_table("MIS_FACT_BRANCH_PLAN")
    except KeyError:
        # Small unit-test repositories may intentionally omit optional plan
        # tables; production configuration always includes them.
        return None
    mi, ai = cols.index("plan_month"), cols.index("plan_amount")
    values = [r[ai] for r in rows if from_ym <= r[mi][:7] <= to_ym]
    return round(sum(values), 2) if values else None


# ---------------------------------------------------------------------------
# Report registry
# ---------------------------------------------------------------------------

SALES_DIMS = [
    {"id": "branch", "label": "Branch"},
    {"id": "region", "label": "Region"},
    {"id": "advisor", "label": "Advisor"},
    {"id": "product", "label": "Product"},
    {"id": "channel", "label": "Channel"},
]
BRANCH_DIMS = [
    {"id": "branch", "label": "Branch"},
    {"id": "region", "label": "Region"},
]
BALANCE_DIMS = [
    {"id": "branch", "label": "Branch"},
    {"id": "region", "label": "Region"},
]


async def overview_synthetic(cache: Any, maps: dict[str, Any], from_ym: str,
                             to_ym: str) -> dict[str, Any]:
    """Top-10 branches table for the overview page."""
    cols, rows = await cache.get_table("MIS_FACT_SALES")
    agg = aggregate(filtered_sales(cols, rows, maps, from_ym, to_ym),
                    [cols.index("branch_id")],
                    [("cnt", "count", None), ("amount", "sum", cols.index("amount"))])
    agg.sort(key=lambda ar: ar.get("amount") or 0, reverse=True)
    plan_by_branch: dict[int, float] = {}
    try:
        pcols, prows = await cache.get_table("MIS_FACT_BRANCH_PLAN")
        pbi, pmi, pai = (pcols.index("branch_id"), pcols.index("plan_month"),
                          pcols.index("plan_amount"))
        for row in prows:
            if from_ym <= row[pmi][:7] <= to_ym:
                plan_by_branch[row[pbi]] = plan_by_branch.get(row[pbi], 0.0) + row[pai]
    except KeyError:
        pass
    data = []
    for ar in agg[:10]:
        b = maps["branches"][ar["__key"][0]]
        r = maps["regions"][b["region_id"]]
        amount = round(ar["amount"], 2)
        plan = plan_by_branch.get(ar["__key"][0])
        data.append([b["branch_code"], b["branch_name"], r["region_name"],
                     ar["cnt"], amount,
                     round(plan, 2) if plan is not None else None,
                     round(amount - plan, 2) if plan is not None else None,
                     round(amount / plan * 100, 2) if plan else None])
    columns = [col("branch_code", "Code"), col("branch_name", "Branch"),
               col("region_name", "Region"), col("cnt", "Sales", "int"),
               col("amount", "Sales value (EUR)", "eur"),
               col("plan", "Plan (EUR)", "eur"),
               col("variance", "Variance (EUR)", "eur"),
               col("attainment", "Attainment %", "pct")]
    return table(columns, data)


async def overview_analytic(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str,
                            dim: str | None) -> dict[str, Any]:
    return table([], [])


def _target_status(value: Any, target: Any) -> str:
    if value is None or target in (None, 0):
        return "neutral"
    ratio = float(value) / float(target) * 100
    if ratio < 80:
        return "critical"
    if ratio < 100:
        return "warning"
    return "good"


async def overview_kpis(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fn in (kpi_sales_period(None, "sales", "Sales in period"),
               kpi_sales_period("Loans", "loan_volume", "Loan volume"),
               kpi_sales_count(None, "units", "Products sold"),
               kpi_movement("customers_new", "acquired", "Clients acquired"),
               kpi_balances(["Current accounts (ROR)"], "ror_balance", "ROR balance"),
               kpi_penetration("penetration", "Products per client"),
               kpi_campaigns_roi("campaign_roi", "Campaign ROI")):
        out.extend(await fn(cache, maps, from_ym, to_ym))
    plan = await network_plan(cache, from_ym, to_ym)
    if plan is not None:
        sales = next((k for k in out if k["key"] == "sales"), None)
        if sales is not None:
            sales["target"] = plan
            sales["target_fmt"] = "eur"
            sales["status"] = _target_status(sales["value"], plan)
            out.insert(1, {"key": "network_plan", "label": "Network plan",
                           "value": plan, "fmt": "eur"})
            out.insert(2, {"key": "network_attainment", "label": "Plan attainment",
                           "value": round(sales["value"] / plan * 100, 2)
                           if plan else None,
                           "fmt": "pct", "target": 100, "target_fmt": "pct",
                           "status": _target_status(sales["value"] / plan * 100
                                                     if plan else None, 100)})
    return out


async def overview_charts(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
    return [
        await chart_sales_vs_plan(cache, maps, from_ym, to_ym),
        await chart_group_share()(cache, maps, from_ym, to_ym),
        await chart_acquisition_churn()(cache, maps, from_ym, to_ym),
        await chart_balances_trend(["Current accounts (ROR)", "Savings accounts",
                                    "Term deposits", "Investments"])(cache, maps, from_ym, to_ym),
        await chart_region_bar("Sales by region")(cache, maps, from_ym, to_ym),
    ]


LOANS_GROUP = "Loans"
INVESTMENTS_GROUP = "Investments"
INSURANCE_GROUP = "Insurance"
ROR_GROUP = "Current accounts (ROR)"
SAVINGS_GROUP = "Savings accounts"
DEPOSITS_GROUP = "Term deposits"


def loans_kpis(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> Any:
    async def build() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for fn in (kpi_sales_period(LOANS_GROUP, "volume", "Loan volume"),
                   kpi_sales_count(LOANS_GROUP, "loans", "Loans"),
                   kpi_sales_average(LOANS_GROUP, "avg_ticket", "Avg ticket", "eur"),
                   kpi_sales_commission(LOANS_GROUP, "commission", "Commission", "eur")):
            out.extend(await fn(cache, maps, from_ym, to_ym))
        return out
    return build()


async def loans_charts(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
    return [
        await chart_sales_trend(LOANS_GROUP)(cache, maps, from_ym, to_ym),
        await chart_share_by("product", "Loan share by product subgroup", LOANS_GROUP)(cache, maps, from_ym, to_ym),
        await chart_region_bar("Loan volume by region", LOANS_GROUP)(cache, maps, from_ym, to_ym),
        await chart_top_entities("advisor", "Top 10 advisors by loan volume", LOANS_GROUP)(cache, maps, from_ym, to_ym),
    ]


async def investments_charts(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
    return [
        await chart_sales_trend(INVESTMENTS_GROUP)(cache, maps, from_ym, to_ym),
        await chart_share_by("product", "Investment share by product", INVESTMENTS_GROUP)(cache, maps, from_ym, to_ym),
        await chart_region_bar("Investment inflows by region", INVESTMENTS_GROUP)(cache, maps, from_ym, to_ym),
        await chart_balances_trend([INVESTMENTS_GROUP])(cache, maps, from_ym, to_ym),
    ]


async def insurance_charts(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
    return [
        await chart_sales_trend(INSURANCE_GROUP)(cache, maps, from_ym, to_ym),
        await chart_share_by("product", "Premium share by product", INSURANCE_GROUP)(cache, maps, from_ym, to_ym),
        await chart_region_bar("Premium by region", INSURANCE_GROUP)(cache, maps, from_ym, to_ym),
    ]


async def ror_charts(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
    cols, rows = await cache.get_table("MIS_FACT_SALES")
    agg = aggregate(filtered_sales(cols, rows, maps, from_ym, to_ym, ROR_GROUP),
                    [cols.index("sale_date")], [("cnt", "count", None)])
    agg.sort(key=lambda r: r["__key"][0])
    labels = [month_label(r["__key"][0]) for r in agg]
    openings = [r["cnt"] for r in agg]
    return [
        chart("openings", "bar", "ROR account openings by month", labels,
              [series("Openings", openings)]),
        await chart_balances_trend([ROR_GROUP])(cache, maps, from_ym, to_ym),
        await chart_balance_region_share([ROR_GROUP], "ROR balance share by region")(cache, maps, from_ym, to_ym),
    ]


async def savings_charts(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
    cols, rows = await cache.get_table("MIS_FACT_SALES")
    agg = aggregate(filtered_sales(cols, rows, maps, from_ym, to_ym, SAVINGS_GROUP),
                    [cols.index("sale_date")], [("cnt", "count", None)])
    agg.sort(key=lambda r: r["__key"][0])
    labels = [month_label(r["__key"][0]) for r in agg]
    return [
        chart("openings", "bar", "Savings account openings by month", labels,
              [series("Openings", [r["cnt"] for r in agg])]),
        await chart_balances_trend([SAVINGS_GROUP])(cache, maps, from_ym, to_ym),
        await chart_balance_region_share([SAVINGS_GROUP], "Savings balance share by region")(cache, maps, from_ym, to_ym),
    ]


async def deposits_charts(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
    return [
        await chart_sales_trend(DEPOSITS_GROUP)(cache, maps, from_ym, to_ym),
        await chart_share_by("product", "New deposits by product", DEPOSITS_GROUP)(cache, maps, from_ym, to_ym),
        await chart_region_bar("New deposits by region", DEPOSITS_GROUP)(cache, maps, from_ym, to_ym),
        await chart_balances_trend([DEPOSITS_GROUP])(cache, maps, from_ym, to_ym),
    ]


async def balances_ror_charts(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
    return [
        await chart_balances_trend([ROR_GROUP])(cache, maps, from_ym, to_ym),
        await chart_balance_region_share([ROR_GROUP], "ROR balance share by region")(cache, maps, from_ym, to_ym),
        await chart_avg_balance_by_branch([ROR_GROUP], "Avg ROR balance by branch")(cache, maps, from_ym, to_ym),
    ]


async def clients_charts(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
    return [
        await chart_acquisition_churn()(cache, maps, from_ym, to_ym),
        await chart_client_base_trend()(cache, maps, from_ym, to_ym),
        await chart_top_branches_by_acquisition()(cache, maps, from_ym, to_ym),
    ]


async def penetration_charts(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
    return [
        await chart_penetration_trend()(cache, maps, from_ym, to_ym),
        await chart_penetration_distribution()(cache, maps, from_ym, to_ym),
        await chart_cross_sell_by_region()(cache, maps, from_ym, to_ym),
    ]


async def campaigns_charts(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
    return [
        await chart_campaign_roi()(cache, maps, from_ym, to_ym),
        await chart_campaign_type_share()(cache, maps, from_ym, to_ym),
        await chart_campaign_funnel()(cache, maps, from_ym, to_ym),
    ]


# ---------------------------------------------------------------------------
# KPI shortcuts for the registry (kept close to their reports)
# ---------------------------------------------------------------------------

def _kpi_list(fns: list[Callable]) -> Callable:
    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for fn in fns:
            out.extend(await fn(cache, maps, from_ym, to_ym))
        return out
    return build


investments_kpis = _kpi_list([
    kpi_sales_period(INVESTMENTS_GROUP, "inflows", "Investment inflows"),
    kpi_sales_count(INVESTMENTS_GROUP, "transactions", "Transactions"),
    kpi_balances([INVESTMENTS_GROUP], "portfolio", "Portfolio balance"),
    kpi_balances_count([INVESTMENTS_GROUP], "accounts", "Portfolio accounts"),
])
balances_ror_kpis = _kpi_list([
    kpi_balances([ROR_GROUP], "balance", "ROR balance"),
    kpi_balances_count([ROR_GROUP], "accounts", "ROR accounts"),
    kpi_balances_avg([ROR_GROUP], "avg_balance", "Avg balance"),
])
insurance_kpis = _kpi_list([
    kpi_sales_count(INSURANCE_GROUP, "policies", "Policies"),
    kpi_sales_period(INSURANCE_GROUP, "premium", "Annual premium"),
    kpi_sales_average(INSURANCE_GROUP, "avg_premium", "Avg premium", "eur"),
    kpi_sales_commission(INSURANCE_GROUP, "commission", "Commission"),
])
ror_kpis = _kpi_list([
    kpi_sales_count(ROR_GROUP, "openings", "Account openings"),
    kpi_sales_period(ROR_GROUP, "initial_deposits", "Initial deposits"),
    kpi_balances_count([ROR_GROUP], "accounts", "ROR accounts"),
    kpi_balances([ROR_GROUP], "balance", "ROR balance"),
])

savings_kpis = _kpi_list([
    kpi_sales_count(SAVINGS_GROUP, "openings", "Account openings"),
    kpi_sales_period(SAVINGS_GROUP, "initial_deposits", "Initial deposits"),
    kpi_balances_count([SAVINGS_GROUP], "accounts", "Savings accounts"),
    kpi_balances([SAVINGS_GROUP], "balance", "Savings balance"),
])
deposits_kpis = _kpi_list([
    kpi_sales_count(DEPOSITS_GROUP, "deposits", "New deposits"),
    kpi_sales_period(DEPOSITS_GROUP, "new_value", "New deposit value"),
    kpi_balances([DEPOSITS_GROUP], "balance", "Deposit balance"),
    kpi_sales_average(DEPOSITS_GROUP, "avg_deposit", "Avg deposit", "eur"),
])
clients_kpis = _kpi_list([
    kpi_movement("customers_end", "base", "Client base"),
    kpi_movement("customers_new", "acquired", "Acquired"),
    kpi_movement("customers_lost", "churned", "Churned"),
    kpi_retention("retention", "Retention %"),
])
penetration_kpis = _kpi_list([
    kpi_penetration("avg_products", "Products per client"),
    kpi_penetration_pct("customers_2plus", "pct_2plus", "2+ products %"),
    kpi_penetration_pct("customers_3plus", "pct_3plus", "3+ products %"),
    kpi_penetration_pct("customers_4plus", "pct_4plus", "4+ products %"),
])
campaigns_kpis = _kpi_list([
    kpi_campaigns_conv("conversions", "Conversions"),
    kpi_campaigns_conversion_rate("conv_rate", "Conversion rate"),
    kpi_campaigns_roi("roi", "Campaign ROI"),
])


# ---------------------------------------------------------------------------
# Report-specific synthetic tables that differ from the generic builders
# ---------------------------------------------------------------------------

def make_sales_balances_monthly(group: str, sales_labels: dict[str, str],
                                 mom_measures: list[str] | None = None) -> Callable:
    """
    Synthetic table factory: monthly sales summary merged with the month-end
    balance snapshot of the same product group (accounts + balance + avg).
    mom_measures: measure names that get a MoM % column ("amount" / "balance").
    """
    mom_measures = mom_measures or []

    async def build(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str) -> dict[str, Any]:
        cols, rows = await cache.get_table("MIS_FACT_SALES")
        agg = aggregate(filtered_sales(cols, rows, maps, from_ym, to_ym, group),
                        [cols.index("sale_date")], _sales_measures(cols))
        bcols, brows = await cache.get_table("MIS_FACT_BALANCES")
        mi, pi, ci, ai = (bcols.index("balance_month"), bcols.index("product_id"),
                          bcols.index("account_count"), bcols.index("balance_amount"))
        bal_by_ym: dict[str, list[float]] = {}
        for r in brows:
            if maps["products"][r[pi]]["product_group"] != group:
                continue
            ym = r[mi][:7]
            d = bal_by_ym.setdefault(ym, [0.0, 0])
            d[0] += r[ai]
            d[1] += r[ci]

        data: list[list[Any]] = []
        by_month = {ar["__key"][0]: ar for ar in agg}
        for ym in month_sequence(from_ym, to_ym):
            ar = by_month.get(ym, {})
            balance, accounts = bal_by_ym.get(ym, [0.0, 0])
            data.append([ym, month_label(ym), ar.get("cnt", 0), ar.get("amount", 0),
                         ar.get("avg_ticket"), ar.get("commission", 0),
                         int(accounts), round(balance, 2),
                         round(balance / accounts, 2) if accounts else None])
        for meas in mom_measures:
            prev: Any = None
            for row in data:
                idx = 3 if meas == "amount" else 7
                v = row[idx]
                row.append(round((v - prev) / prev * 100, 2) if prev else None)
                prev = v

        columns = [col("ym", "Month", "str"), col("month", "Month", "str"),
                   col("cnt", sales_labels.get("cnt", "Count"), "int"),
                   col("amount", sales_labels.get("amount", "Amount (EUR)"), "eur"),
                   col("avg_ticket", sales_labels.get("avg", "Avg amount (EUR)"), "eur"),
                   col("commission", "Commission (EUR)", "eur"),
                   col("accounts", sales_labels.get("accounts", "Accounts"), "int"),
                   col("balance", sales_labels.get("balance", "Balance (EUR)"), "eur"),
                   col("avg_balance", sales_labels.get("avg_balance", "Avg balance (EUR)"), "eur")]
        for meas in mom_measures:
            columns.append(col(f"mom_{meas}", "MoM change %", "pct"))
        return table(columns, data)

    return build


REGISTRY: dict[str, dict[str, Any]] = {
    "overview": {
        "title": "Overview",
        "subtitle": "Retail sales network — headline indicators",
        "kpis": overview_kpis,
        "synthetic": overview_synthetic,
        "analytic": overview_analytic,
        "dims": [],
        "charts": overview_charts,
    },
    "loans": {
        "title": "Loans",
        "subtitle": "Sales reporting — loan products",
        "group": "Loans",
        "kpis": loans_kpis,
        "synthetic": make_sales_monthly(LOANS_GROUP),
        "analytic": make_sales_analytic(LOANS_GROUP),
        "dims": SALES_DIMS,
        "charts": loans_charts,
    },
    "investments": {
        "title": "Investments",
        "subtitle": "Sales and portfolio balances — investment products",
        "group": "Investments",
        "kpis": investments_kpis,
        "synthetic": make_sales_balances_monthly(
            INVESTMENTS_GROUP,
            {"cnt": "Transactions", "amount": "Inflows (EUR)",
             "avg": "Avg inflow (EUR)", "accounts": "Portfolio accounts",
             "balance": "Portfolio balance (EUR)", "avg_balance": "Avg portfolio (EUR)"},
            mom_measures=["balance"]),
        "analytic": make_sales_analytic(INVESTMENTS_GROUP),
        "dims": SALES_DIMS,
        "charts": investments_charts,
    },
    "insurance": {
        "title": "Insurance",
        "subtitle": "Sales reporting — insurance products",
        "group": "Insurance",
        "kpis": insurance_kpis,
        "synthetic": make_sales_monthly(INSURANCE_GROUP),
        "analytic": make_sales_analytic(INSURANCE_GROUP),
        "dims": SALES_DIMS,
        "charts": insurance_charts,
    },
    "current_accounts": {
        "title": "Current accounts (ROR)",
        "subtitle": "Account openings and balances — ROR products",
        "group": "Current accounts (ROR)",
        "kpis": ror_kpis,
        "synthetic": make_sales_balances_monthly(
            ROR_GROUP,
            {"cnt": "Openings", "amount": "Initial deposits (EUR)",
             "avg": "Avg opening deposit (EUR)", "accounts": "Accounts",
             "balance": "Balance (EUR)", "avg_balance": "Avg balance (EUR)"}),
        "analytic": make_sales_analytic(ROR_GROUP),
        "dims": SALES_DIMS,
        "charts": ror_charts,
    },
    "ror_balances": {
        "title": "ROR balances",
        "subtitle": "Balance snapshots — ROR products",
        "kpis": balances_ror_kpis,
        "synthetic": make_balances_monthly([ROR_GROUP]),
        "analytic": make_balances_analytic([ROR_GROUP]),
        "dims": BALANCE_DIMS,
        "charts": balances_ror_charts,
    },
    "savings_accounts": {
        "title": "Savings accounts",
        "subtitle": "Account openings and balances — savings products",
        "group": "Savings accounts",
        "kpis": savings_kpis,
        "synthetic": make_sales_balances_monthly(
            SAVINGS_GROUP,
            {"cnt": "Openings", "amount": "Initial deposits (EUR)",
             "avg": "Avg opening deposit (EUR)", "accounts": "Accounts",
             "balance": "Balance (EUR)", "avg_balance": "Avg balance (EUR)"}),
        "analytic": make_sales_analytic(SAVINGS_GROUP),
        "dims": SALES_DIMS,
        "charts": savings_charts,
    },
    "term_deposits": {
        "title": "Term deposits",
        "subtitle": "New deposits and balances — deposit products",
        "group": "Term deposits",
        "kpis": deposits_kpis,
        "synthetic": make_sales_balances_monthly(
            DEPOSITS_GROUP,
            {"cnt": "New deposits", "amount": "New deposit value (EUR)",
             "avg": "Avg deposit (EUR)", "accounts": "Deposit accounts",
             "balance": "Deposit balance (EUR)", "avg_balance": "Avg balance (EUR)"},
            mom_measures=["amount", "balance"]),
        "analytic": make_sales_analytic(DEPOSITS_GROUP),
        "dims": SALES_DIMS,
        "charts": deposits_charts,
    },
    "clients": {
        "title": "Client acquisition & churn",
        "subtitle": "Monthly client movement across the network",
        "kpis": clients_kpis,
        "synthetic": make_movement_monthly(),
        "analytic": make_movement_analytic(),
        "dims": BRANCH_DIMS,
        "charts": clients_charts,
    },
    "penetration": {
        "title": "Product penetration",
        "subtitle": "Cross-sell and product holdings per client",
        "kpis": penetration_kpis,
        "synthetic": make_penetration_monthly(),
        "analytic": make_penetration_analytic(),
        "dims": BRANCH_DIMS,
        "charts": penetration_charts,
    },
    "campaigns": {
        "title": "Campaign effectiveness",
        "subtitle": "Reach, response, conversion and ROI per campaign",
        "kpis": campaigns_kpis,
        "synthetic": make_campaigns_monthly(),
        "analytic": make_campaigns_analytic(),
        "dims": [{"id": "campaign", "label": "Campaign"},
                 {"id": "branch", "label": "Branch"},
                 {"id": "type", "label": "Campaign type"}],
        "charts": campaigns_charts,
    },
}




# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _enrich_kpis(current: list[dict[str, Any]],
                 previous: list[dict[str, Any]]) -> None:
    """Attach comparable-period deltas without changing existing values."""
    previous_by_key = {item.get("key"): item for item in previous}
    for item in current:
        old = previous_by_key.get(item.get("key"))
        value, old_value = item.get("value"), old.get("value") if old else None
        if not isinstance(value, (int, float)) or not isinstance(old_value, (int, float)):
            continue
        item["delta"] = round(value - old_value, 2)
        item["delta_fmt"] = item.get("fmt", "str")
        item["delta_label"] = "vs previous period"
        item["delta_pct"] = (round((value - old_value) / old_value * 100, 2)
                              if old_value else None)
        if item.get("status", "neutral") == "neutral":
            if item["delta_pct"] is not None and item["delta_pct"] <= -10:
                item["status"] = "warning"
            elif item["delta_pct"] is not None and item["delta_pct"] >= 10:
                item["status"] = "good"


def _insight_number(value: Any, fmt: str) -> str:
    if value is None:
        return "—"
    if fmt == "pct":
        return f"{float(value):.2f}%"
    if fmt == "int":
        return f"{int(value):,}"
    if fmt == "eur":
        return f"€{float(value):,.2f}"
    return str(value)


def _build_insights(report_id: str, kpis: list[dict[str, Any]],
                    synthetic: dict[str, Any],
                    analytic: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Create a small deterministic action list for the report header."""
    insights: list[dict[str, Any]] = []

    for item in kpis:
        status = item.get("status", "neutral")
        if status not in ("critical", "warning"):
            continue
        target = item.get("target")
        if target is not None:
            detail = (f"{item['label']} is {_insight_number(item.get('value'), item.get('fmt', 'str'))} "
                      f"against {_insight_number(target, item.get('target_fmt') or item.get('fmt', 'str'))} target.")
            insights.append({
                "severity": status,
                "title": f"{item['label']} is below target",
                "detail": detail,
                "entity": report_id,
                "action": "Review the lowest-performing entities and current period run-rate.",
            })
        elif item.get("delta_pct") is not None:
            insights.append({
                "severity": status,
                "title": f"{item['label']} is declining",
                "detail": f"{item['label']} is down {abs(item['delta_pct']):.2f}% vs the previous period.",
                "entity": report_id,
                "action": "Open the analytic breakdown to identify the driver.",
            })

    table_def = analytic if analytic and analytic.get("rows") else synthetic
    columns = (table_def or {}).get("columns", [])
    rows = (table_def or {}).get("rows", [])
    amount_idx = next((i for i, c in enumerate(columns)
                       if c.get("key") in ("amount", "sales_value", "balance")), None)
    if rows and amount_idx is not None:
        label_idx = 1 if len(columns) > 1 else 0
        row = rows[0]
        label = row[label_idx] or row[0]
        value = row[amount_idx]
        insights.append({
            "severity": "neutral",
            "title": "Largest contributor",
            "detail": f"{label} contributes {_insight_number(value, columns[amount_idx].get('fmt', 'eur'))}.",
            "entity": str(row[0]) if row else None,
            "action": "Use the row drill-down to inspect the underlying activity.",
        })

    if report_id == "campaigns" and analytic:
        roi_idx = next((i for i, c in enumerate(analytic.get("columns", []))
                        if c.get("key") == "roi_pct"), None)
        negative = [r for r in analytic.get("rows", [])
                    if roi_idx is not None and r[roi_idx] is not None and r[roi_idx] < 0]
        if negative and len(insights) < 3:
            insights.append({
                "severity": "warning",
                "title": "Campaigns with negative ROI",
                "detail": f"{len(negative)} campaign rows have negative return on investment.",
                "entity": "campaigns",
                "action": "Review campaign cost and conversion before increasing reach.",
            })
    return insights[:3]

async def build(report_id: str, cache: Any, from_ym: str | None, to_ym: str | None,
                dim: str | None) -> dict[str, Any]:
    """Assemble the full payload for one report page."""
    spec = REGISTRY.get(report_id)
    if spec is None:
        raise UnknownReport(report_id)

    months = await available_months(cache)
    if not months:
        raise DataNotReady("MIS_FACT_SALES is empty — run: python seed.py")
    if from_ym is None:
        from_ym = months[0]
    if to_ym is None:
        to_ym = months[-1]
    if from_ym > to_ym:
        from_ym, to_ym = to_ym, from_ym

    maps = await build_maps(cache)
    kpis = await spec["kpis"](cache, maps, from_ym, to_ym)
    comparison: tuple[str, str] | None = None
    previous_kpis: list[dict[str, Any]] = []
    previous_from, previous_to = comparable_period(from_ym, to_ym)
    if previous_from >= months[0] and previous_to <= months[-1]:
        comparison = (previous_from, previous_to)
        previous_kpis = await spec["kpis"](cache, maps, previous_from, previous_to)
        _enrich_kpis(kpis, previous_kpis)
    synthetic = await spec["synthetic"](cache, maps, from_ym, to_ym)
    dim_ids = [d["id"] for d in spec["dims"]]
    if dim not in dim_ids:
        dim = dim_ids[0] if dim_ids else None
    analytic = await spec["analytic"](cache, maps, from_ym, to_ym, dim)
    charts = await spec["charts"](cache, maps, from_ym, to_ym)
    insights = _build_insights(report_id, kpis, synthetic, analytic)

    return {
        "id": report_id,
        "title": spec["title"],
        "subtitle": spec["subtitle"],
        "period": {"from": from_ym, "to": to_ym},
        "comparison": ({"from": comparison[0], "to": comparison[1]}
                       if comparison else None),
        "kpis": kpis,
        "insights": insights,
        "synthetic": synthetic,
        "dims": spec["dims"],
        "dim": dim,
        "analytic": analytic,
        "charts": charts,
    }

# ---------------------------------------------------------------------------
# Drill-down (row-level): region -> branches, branch -> advisors,
# advisor -> individual sales. Available for the sales-based reports
# (those with a "group" in the registry).
# ---------------------------------------------------------------------------

class DrillNotAvailable(RuntimeError):
    pass


class DrillKeyNotFound(KeyError):
    pass


def _region_id_by_code(maps: dict[str, Any], code: str) -> int | None:
    for rid, r in maps["regions"].items():
        if r.get("region_code") == code:
            return rid
    return None


def _branch_id_by_code(maps: dict[str, Any], code: str) -> int | None:
    for bid, b in maps["branches"].items():
        if b.get("branch_code") == code:
            return bid
    return None


def _advisor_id_by_code(maps: dict[str, Any], code: str) -> int | None:
    for aid, a in maps["advisors"].items():
        if a.get("advisor_code") == code:
            return aid
    return None


DRILL_TARGETS = {
    "branches": {"dim": "region", "title": "Branches by region"},
    "advisors": {"dim": "branch", "title": "Advisors by branch"},
    "sales": {"dim": "advisor", "title": "Sales by advisor"},
}


async def drill_branches(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str,
                         group: str, region_code: str) -> dict[str, Any]:
    """All branches of one region with their sales summary."""
    rid = _region_id_by_code(maps, region_code)
    if rid is None:
        raise DrillKeyNotFound(f"Unknown region code: {region_code}")
    cols, rows = await cache.get_table("MIS_FACT_SALES")
    bi = cols.index("branch_id")
    f = (r for r in filtered_sales(cols, rows, maps, from_ym, to_ym, group)
         if maps["branches"][r[bi]]["region_id"] == rid)
    agg = aggregate(f, [bi], _sales_measures(cols))
    agg.sort(key=lambda ar: ar.get("amount") or 0, reverse=True)
    data = decorate(agg, maps, "branch", ["cnt", "amount", "avg_ticket", "commission"])
    columns = [col("branch_code", "Code"), col("branch_name", "Branch"),
               col("region_name", "Region"), col("city", "City"),
               col("cnt", "Count", "int"), col("amount", "Amount (EUR)", "eur"),
               col("avg_ticket", "Avg amount (EUR)", "eur"),
               col("commission", "Commission (EUR)", "eur")]
    return table(columns, data)


async def drill_advisors(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str,
                         group: str, branch_code: str) -> dict[str, Any]:
    """All advisors of one branch with their sales summary."""
    bid = _branch_id_by_code(maps, branch_code)
    if bid is None:
        raise DrillKeyNotFound(f"Unknown branch code: {branch_code}")
    cols, rows = await cache.get_table("MIS_FACT_SALES")
    agg = aggregate(filtered_sales(cols, rows, maps, from_ym, to_ym, group, branch_id=bid),
                    [cols.index("advisor_id")], _sales_measures(cols))
    agg.sort(key=lambda ar: ar.get("amount") or 0, reverse=True)
    data = decorate(agg, maps, "advisor", ["cnt", "amount", "avg_ticket", "commission"])
    columns = [col("advisor_code", "Code"), col("advisor_name", "Advisor"),
               col("role", "Role"), col("branch_name", "Branch"),
               col("cnt", "Count", "int"), col("amount", "Amount (EUR)", "eur"),
               col("avg_ticket", "Avg amount (EUR)", "eur"),
               col("commission", "Commission (EUR)", "eur")]
    return table(columns, data)


async def drill_sales(cache: Any, maps: dict[str, Any], from_ym: str, to_ym: str,
                      group: str, advisor_code: str, limit: int = 200) -> dict[str, Any]:
    """Booked sale rows of one advisor (newest first, capped).

    Parent summaries use booked sales, while the ledger remains the place to
    inspect cancelled rows explicitly.
    """
    aid = _advisor_id_by_code(maps, advisor_code)
    if aid is None:
        raise DrillKeyNotFound(f"Unknown advisor code: {advisor_code}")
    cols, rows = await cache.get_table("MIS_FACT_SALES")
    di, bi, ai, pi, ci, cu, si = (cols.index("sale_date"), cols.index("branch_id"),
                                  cols.index("advisor_id"), cols.index("product_id"),
                                  cols.index("channel_id"), cols.index("customer_id"),
                                  cols.index("sale_status"))
    amt, com = cols.index("amount"), cols.index("commission")
    data: list[list[Any]] = []
    for r in rows:
        if r[ai] != aid:
            continue
        if r[si] != "BOOKED":
            continue
        ym = r[di][:7]
        if ym < from_ym or ym > to_ym:
            continue
        if maps["products"][r[pi]]["product_group"] != group:
            continue
        p = maps["products"][r[pi]]
        ch = maps["channels"][r[ci]]
        data.append([r[di], p["product_name"], ch["channel_name"], r[cu],
                     r[amt], r[com], r[si]])
    data.sort(key=lambda row: row[0], reverse=True)
    data = data[:limit]
    columns = [col("sale_date", "Sale date", "str"),
               col("product_name", "Product", "str"),
               col("channel_name", "Channel", "str"),
               col("customer_id", "Customer", "int"),
               col("amount", "Amount (EUR)", "eur"),
               col("commission", "Commission (EUR)", "eur"),
               col("sale_status", "Status", "str")]
    return table(columns, data)


async def drill(report_id: str, cache: Any, from_ym: str | None, to_ym: str | None,
                target: str, key: str) -> dict[str, Any]:
    """Drill-down entry point: target in {branches, advisors, sales}."""
    spec = REGISTRY.get(report_id)
    if spec is None:
        raise UnknownReport(report_id)
    group = spec.get("group")
    if group is None:
        raise DrillNotAvailable(f"Report '{report_id}' has no drill-down")

    months = await available_months(cache)
    if not months:
        raise DataNotReady("MIS_FACT_SALES is empty — run: python seed.py")
    if from_ym is None:
        from_ym = months[0]
    if to_ym is None:
        to_ym = months[-1]
    if from_ym > to_ym:
        from_ym, to_ym = to_ym, from_ym

    maps = await build_maps(cache)
    if target == "branches":
        table_def = await drill_branches(cache, maps, from_ym, to_ym, group, key)
    elif target == "advisors":
        table_def = await drill_advisors(cache, maps, from_ym, to_ym, group, key)
    elif target == "sales":
        table_def = await drill_sales(cache, maps, from_ym, to_ym, group, key)
    else:
        raise DrillNotAvailable(f"Unknown drill target: {target}")

    return {
        "report_id": report_id,
        "target": target,
        "key": key,
        "period": {"from": from_ym, "to": to_ym},
        "title": DRILL_TARGETS[target]["title"],
        "columns": table_def["columns"],
        "rows": table_def["rows"],
    }
