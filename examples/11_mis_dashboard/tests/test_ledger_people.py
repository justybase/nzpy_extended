"""
Tests for the sales ledger (server-side pagination / filtering / sorting)
and the people views (advisor panels, my branch / my results cumulative) —
over the seeded in-memory dataset, no database required.

    python -m pytest tests/ -q
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fakes import FakeRepository  # noqa: E402
from seed import build_dataset  # noqa: E402
from app.services.ledger_service import COLUMN_KEYS, LedgerService  # noqa: E402
from app.services.people_service import (PeopleService, UnknownEntity)  # noqa: E402

LEDGER_COLUMNS = [
    "sale_id", "sale_date", "branch_code", "branch_name", "region_code",
    "advisor_code", "advisor_name", "product_group", "product_name",
    "channel_name", "customer_id", "amount", "commission", "sale_status",
]


@pytest.fixture(scope="module")
def dataset():
    return build_dataset(scale=1)


@pytest.fixture()
def repo(dataset) -> FakeRepository:
    return FakeRepository(dataset)


@pytest.fixture()
def ledger(repo: FakeRepository) -> LedgerService:
    return LedgerService(repo)


@pytest.fixture()
def people(repo: FakeRepository) -> PeopleService:
    return PeopleService(repo, ttl=60)


def _row(payload, i):
    return dict(zip(COLUMN_KEYS, payload["rows"][i]))


# ---------------------------------------------------------------------------
# Ledger: pagination
# ---------------------------------------------------------------------------

async def test_ledger_pagination_math(dataset, ledger: LedgerService) -> None:
    scols, srows = dataset["MIS_FACT_SALES"]
    expected = len(srows)  # no filters -> every sale
    page_size = 25
    p1 = await ledger.query(None, None, None, None, None, None,
                            None, None, 1, page_size)
    assert p1["total"] == expected
    assert p1["pages"] == (expected + page_size - 1) // page_size
    assert len(p1["rows"]) == min(page_size, expected)
    assert p1["page"] == 1

    p2 = await ledger.query(None, None, None, None, None, None,
                            None, None, 2, page_size)
    ids1 = {r[0] for r in p1["rows"]}
    ids2 = {r[0] for r in p2["rows"]}
    assert not (ids1 & ids2), "pages must not overlap"

    # out-of-range page clamps to the last page
    pl = await ledger.query(None, None, None, None, None, None,
                            None, None, 9999, page_size)
    assert pl["page"] == p1["pages"]
    assert len(pl["rows"]) <= page_size


async def test_ledger_default_order_newest_first(dataset, ledger: LedgerService) -> None:
    p = await ledger.query(None, None, None, None, None, None, None, None, 1, 50)
    dates = [r[1] for r in p["rows"]]
    assert dates == sorted(dates, reverse=True)
    # within one day, sale id desc
    for i in range(len(dates) - 1):
        if dates[i] == dates[i + 1]:
            assert p["rows"][i][0] > p["rows"][i + 1][0]


async def test_ledger_sort_by_amount(dataset, ledger: LedgerService) -> None:
    asc = await ledger.query(None, None, None, None, None, None,
                             "amount", "asc", 1, 100)
    amounts = [r[11] for r in asc["rows"]]
    assert amounts == sorted(amounts)

    desc = await ledger.query(None, None, None, None, None, None,
                              "amount", "desc", 1, 100)
    amounts = [r[11] for r in desc["rows"]]
    assert amounts == sorted(amounts, reverse=True)


# ---------------------------------------------------------------------------
# Ledger: filtering
# ---------------------------------------------------------------------------

async def test_ledger_date_window(dataset, ledger: LedgerService) -> None:
    p = await ledger.query("2025-01", "2025-06", None, None, None, None,
                           None, None, 1, 200)
    assert p["total"] > 0
    for r in p["rows"]:
        assert "2025-01-01" <= r[1] <= "2025-06-30"


async def test_ledger_exact_filters(dataset, ledger: LedgerService) -> None:
    p = await ledger.query(None, None, None, "Loans", "Branch", "BOOKED",
                           None, None, 1, 200)
    assert p["total"] > 0
    for r in p["rows"]:
        assert r[7] == "Loans" and r[9] == "Branch" and r[13] == "BOOKED"


async def test_ledger_text_search(dataset, ledger: LedgerService) -> None:
    # an advisor surname from the seeded dimension table
    advisor = dataset["MIS_DIM_ADVISOR"][1][0]
    acols = dataset["MIS_DIM_ADVISOR"][0]
    surname = advisor[acols.index("last_name")]
    p = await ledger.query(None, None, surname, None, None, None,
                           None, None, 1, 200)
    assert p["total"] > 0
    for r in p["rows"]:
        assert surname.lower() in str(r[5]).lower() or surname.lower() in str(r[6]).lower()


async def test_ledger_filters_options(ledger: LedgerService) -> None:
    flt = await ledger.filters()
    assert "Loans" in flt["groups"] and "Current accounts (ROR)" in flt["groups"]
    assert "Branch" in flt["channels"]
    assert flt["statuses"] == ["BOOKED", "CANCELLED"]


async def test_ledger_export_rows_uncapped(dataset, ledger: LedgerService) -> None:
    out = await ledger.export_rows(None, None, None, "Loans", None, "BOOKED")
    assert not out["truncated"]
    assert out["total"] == len(out["rows"]) > 0
    assert [c["key"] for c in out["columns"]] == LEDGER_COLUMNS


# ---------------------------------------------------------------------------
# People: advisor list / panel
# ---------------------------------------------------------------------------

async def test_branch_list(people: PeopleService) -> None:
    branches = await people.branch_list()
    assert len(branches) == 41
    assert branches[0]["code"] and branches[0]["name"]


async def test_advisor_panel_structure(dataset, people: PeopleService) -> None:
    advisor = dataset["MIS_DIM_ADVISOR"][1][0]
    acols = dataset["MIS_DIM_ADVISOR"][0]
    code = advisor[acols.index("advisor_code")]
    panel = await people.advisor_panel(code)

    assert panel["code"] == code
    assert panel["info"]["branch_code"]
    assert panel["rows"] and len(panel["rows"][0]) == len(panel["columns"])
    # rows are chronological, attainment == achieved / plan
    for i, row in enumerate(panel["rows"]):
        if i:
            assert row[0] > panel["rows"][i - 1][0]
        plan, achieved, attainment = row[2], row[3], row[4]
        expected = round(achieved / plan * 100, 2) if plan else None
        assert attainment == expected or (attainment is None and expected is None)
        assert 1 <= row[9] <= 5
    # summary reflects the latest month
    assert panel["summary"]["latest_month"] == panel["rows"][-1][0]
    assert panel["summary"]["latest_rating"] == panel["rows"][-1][9]
    assert panel["charts"] and panel["charts"][0]["series"][0]["data"]


async def test_advisor_panel_unknown(people: PeopleService) -> None:
    with pytest.raises(UnknownEntity):
        await people.advisor_panel("NOPE00")


# ---------------------------------------------------------------------------
# People: cumulative (my branch / my results)
# ---------------------------------------------------------------------------

def _booked_sums(dataset, entity_idx: int, entity_id: int):
    """(month -> day -> amount) for one entity from the raw sales rows."""
    scols = dataset["MIS_FACT_SALES"][0]
    si, ei, di, ami = (scols.index("sale_status"), entity_idx,
                       scols.index("sale_date"), scols.index("amount"))
    out: dict[str, dict[int, float]] = {}
    for r in dataset["MIS_FACT_SALES"][1]:
        if r[si] != "BOOKED" or r[ei] != entity_id:
            continue
        ym, day = r[di][:7], int(r[di][8:10])
        # note: the RHS is evaluated before the subscript target, so the
        # setdefault call cannot be inlined (first lookup would miss)
        inner = out.setdefault(ym, {})
        inner[day] = inner.get(day, 0.0) + r[ami]
    return out


async def test_cumulative_branch_math(dataset, people: PeopleService) -> None:
    branch = dataset["MIS_DIM_BRANCH"][1][0]
    bcols = dataset["MIS_DIM_BRANCH"][0]
    code = branch[bcols.index("branch_code")]
    month = "2026-08"
    payload = await people.cumulative("branch", code, month)

    assert payload["scope"] == "branch"
    assert payload["info"]["code"] == code
    assert payload["kpis"][0]["key"] == "mtd" and payload["kpis"][0]["value"] > 0

    # independent daily sums from the raw dataset (sales-column index!)
    scols = dataset["MIS_FACT_SALES"][0]
    daily = _booked_sums(dataset, scols.index("branch_id"),
                         branch[bcols.index("branch_id")])[month]
    days = len(payload["rows"])
    assert days == 31
    run = 0.0
    for i, row in enumerate(payload["rows"]):
        day = i + 1
        run = round(run + round(daily.get(day, 0.0), 2), 2)
        assert row[1] == day
        assert row[3] == run                      # cumulative
        # plan prorated by day
        assert row[4] == round(payload["kpis"][1]["value"] * day / days, 2)
        if row[4]:
            assert row[5] == round(run / row[4] * 100, 2)
    # final attainment KPI
    kpis = {k["key"]: k["value"] for k in payload["kpis"]}
    assert kpis["mtd"] == run
    assert kpis["attainment"] == round(run / kpis["plan"] * 100, 2)
    assert payload["charts"][0]["series"][0]["data"][-1] == run


async def test_cumulative_vs_prev_month(dataset, people: PeopleService) -> None:
    branch = dataset["MIS_DIM_BRANCH"][1][0]
    bcols = dataset["MIS_DIM_BRANCH"][0]
    code = branch[bcols.index("branch_code")]
    payload = await people.cumulative("branch", code, "2026-08")

    scols = dataset["MIS_FACT_SALES"][0]
    prev = _booked_sums(dataset, scols.index("branch_id"),
                        branch[bcols.index("branch_id")]).get("2026-07", {})
    prev_run = 0.0
    for i, row in enumerate(payload["rows"]):
        prev_run = round(prev_run + round(prev.get(i + 1, 0.0), 2), 2)
        assert row[6] == prev_run                  # prev month cumulative
        if prev_run:
            assert row[7] == round((row[3] - prev_run) / prev_run * 100, 2)
    kpis = {k["key"]: k["value"] for k in payload["kpis"]}
    if prev_run:
        assert kpis["vs_prev"] == round((payload["rows"][-1][3] - prev_run)
                                        / prev_run * 100, 2)


async def test_cumulative_advisor_uses_perf_plan(dataset, people: PeopleService) -> None:
    advisor = dataset["MIS_DIM_ADVISOR"][1][0]
    acols = dataset["MIS_DIM_ADVISOR"][0]
    code = advisor[acols.index("advisor_code")]
    month = "2026-08"
    payload = await people.cumulative("advisor", code, month)

    pcols = dataset["MIS_FACT_ADVISOR_PERF"][0]
    perf_plan = next(r[pcols.index("plan_amount")]
                     for r in dataset["MIS_FACT_ADVISOR_PERF"][1]
                     if r[pcols.index("advisor_id")] == advisor[acols.index("advisor_id")]
                     and r[pcols.index("perf_month")] == "2026-08-01")
    kpis = {k["key"]: k["value"] for k in payload["kpis"]}
    assert kpis["plan"] == perf_plan
    # advisor cumulative == the panel's achieved for that month
    panel = await people.advisor_panel(code)
    assert panel["rows"][-1][3] == kpis["mtd"]


async def test_cumulative_errors(people: PeopleService) -> None:
    with pytest.raises(UnknownEntity):
        await people.cumulative("branch", "NOPE00", "2026-08")
    with pytest.raises(ValueError):
        await people.cumulative("branch", "DUB01", "2026-13")
    with pytest.raises(ValueError):
        await people.cumulative("team", "DUB01", "2026-08")


async def test_panel_and_cumulative_cached(repo: FakeRepository,
                                           people: PeopleService) -> None:
    # payloads are cached: a repeat call only pays the two authorization
    # lookups (advisor + branch), never the aggregation work
    await people.advisor_panel("P0001")
    calls = repo.load_count
    await people.advisor_panel("P0001")
    assert repo.load_count == calls + 2

    await people.cumulative("branch", "DUB01", "2026-08")
    calls = repo.load_count
    await people.cumulative("branch", "DUB01", "2026-08")
    assert repo.load_count == calls + 1  # one authorization lookup only