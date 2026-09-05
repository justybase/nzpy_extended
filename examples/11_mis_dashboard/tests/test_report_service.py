"""
Unit tests for the report/drill/export services — no database required.

The MISRepository interface (app/repositories/base.py) is implemented by a
tiny in-memory FakeRepository, which is exactly how these services can be
tested in a real project: business logic never touches Netezza directly.

Run from the example directory:
    python -m pytest tests/ -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fakes import FakeRepository  # noqa: E402
from app.services import reporting  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.services.export_service import ExportService  # noqa: E402
from app.services.report_service import ReportService  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory repository (same interface as CachedMISRepository)
# ---------------------------------------------------------------------------

PRODUCT_COLS = ["product_id", "product_code", "product_name", "product_group",
                "product_subgroup", "is_balance_product", "min_amount",
                "max_amount", "commission_rate"]
PRODUCTS = [
    [1, "KRED_GOT", "Cash loan", "Loans", "Cash loans", 0, 100, 50000, 0.008],
    [2, "ROR_STD", "ROR current account", "Current accounts (ROR)", "Standard ROR", 1, 100, 3000, 0.0],
]
REGION_COLS = ["region_id", "region_code", "region_name", "area_name", "seat_city"]
REGIONS = [[1, "RCEN", "Central Region", "Dublin Area", "Dublin"]]
BRANCH_COLS = ["branch_id", "branch_code", "branch_name", "region_id", "city",
               "district", "open_date", "status"]
BRANCHES = [[1, "DUB01", "Branch Dublin City Centre", 1, "Dublin", "City Centre",
             "2012-01-01", "ACTIVE"]]
ADVISOR_COLS = ["advisor_id", "advisor_code", "first_name", "last_name",
                "branch_id", "role", "hire_date", "status"]
ADVISORS = [
    [1, "P0001", "Sean", "Murphy", 1, "Client Advisor", "2015-01-01", "ACTIVE"],
    [2, "P0002", "Aoife", "Kelly", 1, "Investment Advisor", "2016-01-01", "ACTIVE"],
]
CHANNEL_COLS = ["channel_id", "channel_name"]
CHANNELS = [[1, "Branch"]]
CAMPAIGN_COLS = ["campaign_id", "campaign_code", "campaign_name", "campaign_type",
                 "target_segment", "product_id", "start_date", "end_date", "budget"]
CAMPAIGNS = [[1, "CMP001", "Email · Cash loan · New clients", "Email",
              "New clients", 1, "2025-09-01", "2025-10-01", 100.0]]
SALES_COLS = ["sale_id", "sale_date", "branch_id", "advisor_id", "product_id",
              "channel_id", "customer_id", "amount", "commission", "sale_status"]
SALES = [
    [1, "2025-09-05", 1, 1, 1, 1, 100001, 10000.0, 80.0, "BOOKED"],
    [2, "2025-09-20", 1, 2, 1, 1, 100002, 20000.0, 160.0, "BOOKED"],
    [3, "2025-10-03", 1, 1, 1, 1, 100003, 15000.0, 120.0, "BOOKED"],
    [4, "2025-10-15", 1, 1, 2, 1, 100004, 500.0, 0.0, "CANCELLED"],
]
BALANCE_COLS = ["balance_month", "branch_id", "product_id", "account_count", "balance_amount"]
BALANCES = [
    ["2025-09-01", 1, 2, 5, 21000.0],
    ["2025-10-01", 1, 2, 6, 25000.0],
]
MOVEMENT_COLS = ["movement_month", "branch_id", "customers_start", "customers_new",
                 "customers_lost", "customers_end", "product_relations",
                 "customers_2plus", "customers_3plus", "customers_4plus"]
MOVEMENT = [
    ["2025-09-01", 1, 100, 3, 1, 102, 200, 40, 10, 2],
    ["2025-10-01", 1, 102, 4, 2, 104, 220, 45, 12, 3],
]
RESULT_COLS = ["campaign_id", "branch_id", "contacts", "responses",
               "conversions", "sales_amount", "cost"]
RESULTS = [[1, 1, 1000, 100, 20, 50000.0, 500.0]]

TABLES = {
    "MIS_DIM_PRODUCT": (PRODUCT_COLS, PRODUCTS),
    "MIS_DIM_REGION": (REGION_COLS, REGIONS),
    "MIS_DIM_BRANCH": (BRANCH_COLS, BRANCHES),
    "MIS_DIM_ADVISOR": (ADVISOR_COLS, ADVISORS),
    "MIS_DIM_CHANNEL": (CHANNEL_COLS, CHANNELS),
    "MIS_DIM_CAMPAIGN": (CAMPAIGN_COLS, CAMPAIGNS),
    "MIS_FACT_SALES": (SALES_COLS, SALES),
    "MIS_FACT_BALANCES": (BALANCE_COLS, BALANCES),
    "MIS_FACT_CUSTOMER_MOVEMENT": (MOVEMENT_COLS, MOVEMENT),
    "MIS_FACT_CAMPAIGN_RESULTS": (RESULT_COLS, RESULTS),
}


@pytest.fixture
def repo() -> FakeRepository:
    return FakeRepository(TABLES)


@pytest.fixture
def service(repo: FakeRepository) -> ReportService:
    return ReportService(repo, ttl=60)


# ---------------------------------------------------------------------------
# Registry & payload building
# ---------------------------------------------------------------------------

def test_registry_contains_all_reports() -> None:
    assert len(reporting.REGISTRY) == 11
    for rid in ("overview", "loans", "investments", "insurance", "current_accounts",
                "ror_balances", "savings_accounts", "term_deposits", "clients",
                "penetration", "campaigns"):
        assert rid in reporting.REGISTRY


def test_settings_preload_every_seeded_table() -> None:
    assert set(Settings().table_names) >= {
        "MIS_FACT_BRANCH_PLAN", "MIS_FACT_ADVISOR_PERF", "MIS_DIM_USER",
    }


async def test_loans_report_shape(service: ReportService) -> None:
    payload = await service.build("loans", "2025-09", "2025-10", "branch")
    assert payload["title"] == "Loans"
    assert len(payload["kpis"]) == 4
    assert payload["kpis"][0]["key"] == "volume"
    assert payload["kpis"][0]["value"] == 45000.0  # 10k + 20k + 15k booked
    # synthetic: one row per month in range
    assert [r[0] for r in payload["synthetic"]["rows"]] == ["2025-09", "2025-10"]
    # cancelled sale (ROR) excluded and loans only
    assert payload["synthetic"]["rows"][-1][2] == 1  # September: 1 booked loan
    # analytic by branch: one branch
    assert len(payload["analytic"]["rows"]) == 1
    assert payload["analytic"]["rows"][0][1] == "Branch Dublin City Centre"
    # charts present
    assert {c["id"] for c in payload["charts"]} == {"trend", "share", "by_region", "top"}


async def test_sales_kpis_use_their_named_measure(service: ReportService) -> None:
    payload = await service.build("loans", "2025-09", "2025-10", "branch")
    kpis = {k["key"]: k for k in payload["kpis"]}
    assert kpis["volume"]["value"] == 45000.0
    assert kpis["loans"]["value"] == 3
    assert kpis["avg_ticket"]["value"] == 15000.0
    assert kpis["commission"]["value"] == 360.0
    assert kpis["avg_ticket"]["value"] != kpis["volume"]["value"]


async def test_report_payload_exposes_insights_and_comparison(
        service: ReportService) -> None:
    payload = await service.build("loans", "2025-09", "2025-10", "branch")
    assert "insights" in payload
    assert "freshness" in payload
    # The tiny fixture has no complete preceding period, so comparison is
    # explicitly absent rather than silently comparing unlike ranges.
    assert payload["comparison"] is None


async def test_unknown_report_raises(service: ReportService) -> None:
    with pytest.raises(reporting.UnknownReport):
        await service.build("nope", "2025-09", "2025-10", "branch")


async def test_period_is_clamped_to_available_months(service: ReportService) -> None:
    payload = await service.build("loans", None, None, "branch")
    assert payload["period"] == {"from": "2025-09", "to": "2025-10"}


# ---------------------------------------------------------------------------
# Report payload caching
# ---------------------------------------------------------------------------

async def test_report_cache_serves_second_call_without_recomputation(
        repo: FakeRepository, service: ReportService) -> None:
    await service.build("loans", "2025-09", "2025-10", "branch")
    calls_after_first = repo.load_count
    await service.build("loans", "2025-09", "2025-10", "branch")
    assert repo.load_count == calls_after_first  # served from TTLCache

    # a different period must recompute
    await service.build("loans", "2025-09", "2025-09", "branch")
    assert repo.load_count > calls_after_first


# ---------------------------------------------------------------------------
# Drill-down
# ---------------------------------------------------------------------------

async def test_drill_advisors_by_branch(service: ReportService) -> None:
    payload = await service.drill("loans", "advisors", "DUB01", "2025-09", "2025-10")
    assert payload["target"] == "advisors"
    rows = {r[0]: r for r in payload["rows"]}
    assert set(rows) == {"P0001", "P0002"}
    assert rows["P0001"][4] == 2      # two booked loans
    assert rows["P0001"][5] == 25000.0  # 10k + 15k


async def test_drill_sales_of_advisor(service: ReportService) -> None:
    payload = await service.drill("loans", "sales", "P0001", "2025-09", "2025-10")
    # newest first, full sale date kept, only loans of that advisor
    assert len(payload["rows"]) == 2
    assert payload["rows"][0][0] == "2025-10-03"
    assert payload["rows"][0][1] == "Cash loan"
    assert payload["rows"][0][6] == "BOOKED"
    assert all(row[6] == "BOOKED" for row in payload["rows"])
    assert payload["rows"][1][0] == "2025-09-05"
    assert payload["rows"][1][4] == 10000.0


async def test_drill_branches_by_region(service: ReportService) -> None:
    payload = await service.drill("loans", "branches", "RCEN", "2025-09", "2025-10")
    assert len(payload["rows"]) == 1
    assert payload["rows"][0][0] == "DUB01"
    assert payload["rows"][0][5] == 45000.0


async def test_drill_unavailable_for_overview(service: ReportService) -> None:
    with pytest.raises(reporting.DrillNotAvailable):
        await service.drill("overview", "advisors", "DUB01", "2025-09", "2025-10")


async def test_drill_unknown_key(service: ReportService) -> None:
    with pytest.raises(reporting.DrillKeyNotFound):
        await service.drill("loans", "advisors", "NOPE01", "2025-09", "2025-10")


# ---------------------------------------------------------------------------
# Exports (xlspy, no DB)
# ---------------------------------------------------------------------------

async def test_export_xlsx_and_xlsb(repo: FakeRepository) -> None:
    from xlspy import ExcelReader

    export_service = ExportService(ReportService(repo, ttl=60))
    for fmt in ("xlsx", "xlsb"):
        result = await export_service.export_report("loans", "synthetic", fmt,
                                                    "2025-09", "2025-10", None)
        assert result.filename.endswith(f".{fmt}")
        try:
            with ExcelReader(result.path) as reader:
                names = reader.get_sheet_names()
                rows = reader.read_all(names[0])
            assert names[-1] == "Report info"
            # 'ym' key column is dropped from the export
            assert rows[0] == ["Month", "Count", "Amount (EUR)", "Avg amount (EUR)",
                               "Commission (EUR)", "MoM change %"]
            assert len(rows) == 3  # header + two months
        finally:
            import os
            os.remove(result.path)


async def test_export_drill_xlsx(repo: FakeRepository) -> None:
    from xlspy import ExcelReader

    export_service = ExportService(ReportService(repo, ttl=60))
    result = await export_service.export_drill("loans", "advisors", "DUB01",
                                               "xlsx", "2025-09", "2025-10")
    try:
        with ExcelReader(result.path) as reader:
            rows = reader.read_all(reader.get_sheet_names()[0])
        assert rows[0][1] == "Advisor"
        assert len(rows) == 3  # header + P0001 + P0002
    finally:
        import os
        os.remove(result.path)
