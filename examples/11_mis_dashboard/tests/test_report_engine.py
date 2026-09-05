"""
Report-engine tests over a small SEEDED dataset — no database required.

The dataset is produced by the real seed generators (seed.build_dataset),
so these tests exercise the actual data model + the whole engine:
every report x every analytic dimension, drill-down, numeric sanity checks
(cross-checked with independent computations) and xlsx/xlsb exports.

    python -m pytest tests/ -q
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fakes import FakeRepository  # noqa: E402
from seed import build_dataset  # noqa: E402
from app.services import reporting  # noqa: E402
from app.services.export_service import ExportService  # noqa: E402
from app.services.report_service import ReportService  # noqa: E402

SALES_DIMS = ["branch", "region", "advisor", "product", "channel"]
BRANCH_DIMS = ["branch", "region"]

REPORT_DIMS = {
    "overview": [],
    "loans": SALES_DIMS,
    "investments": SALES_DIMS,
    "insurance": SALES_DIMS,
    "current_accounts": SALES_DIMS,
    "ror_balances": BRANCH_DIMS,
    "savings_accounts": SALES_DIMS,
    "term_deposits": SALES_DIMS,
    "clients": BRANCH_DIMS,
    "penetration": BRANCH_DIMS,
    "campaigns": ["campaign", "branch", "type"],
}


@pytest.fixture(scope="module")
def dataset():
    return build_dataset(scale=1)


@pytest.fixture()
def repo(dataset) -> FakeRepository:
    return FakeRepository(dataset)


@pytest.fixture()
def service(repo: FakeRepository) -> ReportService:
    return ReportService(repo, ttl=60)


@pytest.fixture()
def export_service(repo: FakeRepository) -> ExportService:
    return ExportService(ReportService(repo, ttl=60))


# ---------------------------------------------------------------------------
# The seeded dataset itself
# ---------------------------------------------------------------------------

def test_dataset_is_well_formed(dataset) -> None:
    expected = {"MIS_DIM_REGION", "MIS_DIM_BRANCH", "MIS_DIM_ADVISOR",
                "MIS_DIM_PRODUCT", "MIS_DIM_CHANNEL", "MIS_DIM_CAMPAIGN",
                "MIS_FACT_SALES", "MIS_FACT_BALANCES",
                "MIS_FACT_CUSTOMER_MOVEMENT", "MIS_FACT_CAMPAIGN_RESULTS",
                "MIS_FACT_BRANCH_PLAN", "MIS_FACT_ADVISOR_PERF",
                "MIS_DIM_USER"}
    assert set(dataset) == expected
    for name, (cols, rows) in dataset.items():
        assert cols, name
        assert rows, name
    # sales: at least one booked and one cancelled row, plausible dates
    cols, rows = dataset["MIS_FACT_SALES"]
    statuses = {r[cols.index("sale_status")] for r in rows}
    assert {"BOOKED", "CANCELLED"} <= statuses
    dates = [r[cols.index("sale_date")] for r in rows]
    assert min(dates) >= "2024-01-01" and max(dates) <= "2026-08-31"
    # all six product groups present
    pcols, prows = dataset["MIS_DIM_PRODUCT"]
    groups = {r[pcols.index("product_group")] for r in prows}
    assert groups == {"Loans", "Investments", "Insurance", "Current accounts (ROR)",
                      "Savings accounts", "Term deposits"}


# ---------------------------------------------------------------------------
# Every report builds (and every analytic dimension resolves)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("report_id", sorted(REPORT_DIMS))
async def test_report_builds(report_id: str, service: ReportService) -> None:
    payload = await service.build(report_id, None, None, None)
    assert payload["id"] == report_id
    assert payload["title"]
    assert payload["period"]["from"] <= payload["period"]["to"]
    assert payload["kpis"]
    assert payload["synthetic"]["columns"] and payload["synthetic"]["rows"]
    assert payload["charts"]


@pytest.mark.parametrize("report_id,dim", [
    (rid, dim) for rid, dims in REPORT_DIMS.items() for dim in dims
])
async def test_analytic_dimension(report_id: str, dim: str, service: ReportService) -> None:
    payload = await service.build(report_id, None, None, dim)
    table = payload["analytic"]
    assert table is not None and table["columns"], f"{report_id}/{dim} produced no table"
    assert table["rows"], f"{report_id}/{dim} produced no rows"
    # label columns + measure columns are consistent with the rows
    assert len(table["columns"]) == len(table["rows"][0])


async def test_available_months(dataset, service: ReportService) -> None:
    cols, rows = dataset["MIS_FACT_SALES"]
    expected = sorted({r[cols.index("sale_date")][:7] for r in rows})
    assert await service.available_months() == expected


async def test_overview_has_plan_comparison_and_decision_fields(
        service: ReportService) -> None:
    payload = await service.build("overview", "2025-09", "2026-08", None)
    kpis = {k["key"]: k for k in payload["kpis"]}
    assert kpis["network_plan"]["value"] > 0
    assert kpis["sales"]["target"] == kpis["network_plan"]["value"]
    assert payload["comparison"] == {"from": "2024-09", "to": "2025-08"}
    assert payload["synthetic"]["columns"][-1]["key"] == "attainment"
    sales_chart = next(c for c in payload["charts"] if c["id"] == "sales_plan")
    assert [s["name"] for s in sales_chart["series"]] == ["Actual (EUR)", "Plan (EUR)"]


# ---------------------------------------------------------------------------
# Drill-down on every sales report
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("report_id", [
    "loans", "investments", "insurance", "current_accounts",
    "savings_accounts", "term_deposits",
])
async def test_drill_down(report_id: str, dataset, service: ReportService) -> None:
    region_code = dataset["MIS_DIM_REGION"][1][0][1]
    branch_code = dataset["MIS_DIM_BRANCH"][1][0][1]
    advisor_code = dataset["MIS_DIM_ADVISOR"][1][0][1]

    branches = await service.drill(report_id, "branches", region_code, None, None)
    # ranked by amount desc, so the dim-table order is not preserved
    assert branches["rows"] and branch_code in {r[0] for r in branches["rows"]}

    advisors = await service.drill(report_id, "advisors", branch_code, None, None)
    assert advisors["rows"] and {r[0] for r in advisors["rows"]} >= {advisor_code}

    sales = await service.drill(report_id, "sales", advisor_code, None, None)
    assert sales["rows"]
    # full dates, newest first
    assert "-" in sales["rows"][0][0] and len(sales["rows"][0][0]) == 10


# ---------------------------------------------------------------------------
# Numeric sanity — cross-checked with independent computations
# ---------------------------------------------------------------------------

def _sales_total(dataset, group: str, from_ym: str, to_ym: str) -> float:
    pcols, prows = dataset["MIS_DIM_PRODUCT"]
    groups = {r[0]: r[3] for r in prows}
    cols, rows = dataset["MIS_FACT_SALES"]
    pi, si, di, ai = (cols.index("product_id"), cols.index("sale_status"),
                      cols.index("sale_date"), cols.index("amount"))
    return sum(r[ai] for r in rows
               if groups[r[pi]] == group and r[si] == "BOOKED"
               and from_ym <= r[di][:7] <= to_ym)


async def test_loans_numbers_are_exact(dataset, service: ReportService) -> None:
    months = await service.available_months()
    from_ym, to_ym = months[0], months[-1]
    payload = await service.build("loans", from_ym, to_ym, "branch")

    # synthetic monthly amounts sum to the independent total
    monthly = [r[3] for r in payload["synthetic"]["rows"]]  # amount column
    assert sum(monthly) == pytest.approx(_sales_total(dataset, "Loans", from_ym, to_ym))

    # the volume KPI matches too
    kpis = {k["key"]: k["value"] for k in payload["kpis"]}
    assert kpis["volume"] == pytest.approx(_sales_total(dataset, "Loans", from_ym, to_ym))

    # MoM column exists and every month has non-negative counts
    assert "MoM change %" in [c["label"] for c in payload["synthetic"]["columns"]]
    assert all(r[2] > 0 for r in payload["synthetic"]["rows"])


def _balance_total(dataset, group: str, month: str) -> float:
    pcols, prows = dataset["MIS_DIM_PRODUCT"]
    groups = {r[0]: r[3] for r in prows}
    cols, rows = dataset["MIS_FACT_BALANCES"]
    pi, mi, ai = (cols.index("product_id"), cols.index("balance_month"),
                  cols.index("balance_amount"))
    return sum(r[ai] for r in rows if groups[r[pi]] == group and r[mi][:7] == month)


async def test_ror_balances_are_exact(dataset, service: ReportService) -> None:
    months = await service.available_months()
    to_ym = months[-1]
    payload = await service.build("ror_balances", months[0], to_ym, "branch")
    last_row = payload["synthetic"]["rows"][-1]
    # columns: ym, month, accounts, balance, avg_balance, mom
    assert last_row[3] == pytest.approx(_balance_total(dataset, "Current accounts (ROR)", to_ym))
    assert last_row[2] > 0
    assert last_row[4] == pytest.approx(last_row[3] / last_row[2])


async def test_movement_metrics_in_bounds(service: ReportService) -> None:
    payload = await service.build("clients", None, None, "branch")
    cols = [c["key"] for c in payload["synthetic"]["columns"]]
    for row in payload["synthetic"]["rows"]:
        d = dict(zip(cols, row))
        assert d["start"] >= 0 and d["end"] > 0
        assert d["end"] == d["start"] + d["new"] - d["lost"]
        assert 0 <= d["retention_pct"] <= 100
        assert 0 <= d["churn_pct"] <= 100


async def test_penetration_branch_rows_match_columns(service: ReportService) -> None:
    payload = await service.build("penetration", None, None, "branch")
    table = payload["analytic"]
    cols = table["columns"]
    for row in table["rows"]:
        assert len(row) == len(cols)
        d = dict(zip([c["key"] for c in cols], row))
        assert d["end"] is not None and d["end"] > 0          # Clients column present
        assert 1.0 <= d["products_per_client"] <= 5.0
        assert 0 <= d["pct_4plus"] <= d["pct_3plus"] <= d["pct_2plus"] <= 100


async def test_penetration_metrics_in_bounds(service: ReportService) -> None:
    payload = await service.build("penetration", None, None, "region")
    cols = [c["key"] for c in payload["synthetic"]["columns"]]
    for row in payload["synthetic"]["rows"]:
        d = dict(zip(cols, row))
        assert 1.0 <= d["products_per_client"] <= 5.0
        assert 0 <= d["pct_2plus"] <= 100
        assert 0 <= d["pct_3plus"] <= 100
        assert 0 <= d["pct_4plus"] <= 100
        assert d["pct_4plus"] <= d["pct_3plus"] <= d["pct_2plus"]


async def test_campaign_kpis_present(service: ReportService) -> None:
    payload = await service.build("campaigns", None, None, "campaign")
    kpis = {k["key"]: k["value"] for k in payload["kpis"]}
    assert kpis["conversions"] > 0
    assert kpis["conv_rate"] is not None and 0 <= kpis["conv_rate"] <= 100
    assert kpis["roi"] is not None
    # per-campaign analytic has the ROI column populated
    assert all(r[-1] is not None for r in payload["analytic"]["rows"])


# ---------------------------------------------------------------------------
# Exports for every report / kind / format (xlspy round-trip)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("report_id", sorted(REPORT_DIMS))
@pytest.mark.parametrize("kind", ["synthetic", "analytic"])
@pytest.mark.parametrize("fmt", ["xlsx", "xlsb"])
async def test_exports_roundtrip(report_id: str, kind: str, fmt: str,
                                 export_service: ExportService) -> None:
    from xlspy import ExcelReader

    dim = REPORT_DIMS[report_id][0] if REPORT_DIMS[report_id] else None
    if kind == "analytic" and not REPORT_DIMS[report_id]:
        # overview declares no analytic table -> the API must reject the export
        with pytest.raises(ValueError):
            await export_service.export_report(report_id, kind, fmt, None, None, dim)
        return
    result = await export_service.export_report(report_id, kind, fmt, None, None, dim)
    try:
        assert result.filename.endswith(f".{fmt}")
        with ExcelReader(result.path) as reader:
            names = reader.get_sheet_names()
            rows = reader.read_all(names[0])
        assert names[-1] == "Report info"
        assert rows and len(rows[0]) > 1          # header present
        assert len(rows) > 1                        # at least one data row
    finally:
        os.remove(result.path)


@pytest.mark.parametrize("fmt", ["xlsx", "xlsb"])
async def test_drill_export_roundtrip(fmt: str, dataset,
                                      export_service: ExportService) -> None:
    from xlspy import ExcelReader

    branch_code = dataset["MIS_DIM_BRANCH"][1][0][1]
    result = await export_service.export_drill("loans", "advisors", branch_code,
                                               fmt, None, None)
    try:
        with ExcelReader(result.path) as reader:
            rows = reader.read_all(reader.get_sheet_names()[0])
        assert rows[0][1] == "Advisor"
        assert len(rows) > 2  # header + several advisors
    finally:
        os.remove(result.path)


# ---------------------------------------------------------------------------
# Report payload caching over the seeded dataset
# ---------------------------------------------------------------------------

async def test_payload_cache_no_extra_repository_calls(repo: FakeRepository,
                                                       service: ReportService) -> None:
    await service.build("loans", None, None, "branch")
    calls = repo.load_count
    await service.build("loans", None, None, "branch")
    assert repo.load_count == calls

    await service.drill("loans", "advisors", "DUB01", None, None)
    calls = repo.load_count
    await service.drill("loans", "advisors", "DUB01", None, None)
    assert repo.load_count == calls
