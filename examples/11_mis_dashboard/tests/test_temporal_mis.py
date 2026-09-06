"""Point-in-time MIS semantics over the deterministic reference mart."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.people_service import PeopleService  # noqa: E402
from app.core.roles import SessionUser  # noqa: E402
from app.repositories.temporal import AsOfMISRepository  # noqa: E402
from app.services.temporal_service import (SnapshotNotFound,  # noqa: E402
                                           TemporalMISService)
from fakes import FakeRepository  # noqa: E402
from seed import build_dataset  # noqa: E402


@pytest.fixture(scope="module")
def dataset():
    return build_dataset(scale=1)


@pytest.fixture()
def service(dataset) -> TemporalMISService:
    return TemporalMISService(FakeRepository(dataset), ttl=60)


async def test_latest_snapshot_is_true_partial_month(service: TemporalMISService) -> None:
    snapshots = await service.available_snapshots()
    assert snapshots[-1] == "2026-08-15"
    assert "2026-08-16" not in snapshots


async def test_mtd_reconciles_to_booked_source(dataset, service: TemporalMISService) -> None:
    payload = await service.performance("2026-08-15", "historical", "branch")
    mtd = next(k["value"] for k in payload["kpis"] if k["key"] == "mtd")
    cols, rows = dataset["MIS_FACT_SALES"]
    expected = sum(row[cols.index("amount")] for row in rows
                   if row[cols.index("sale_status")] == "BOOKED"
                   and "2026-08-01" <= row[cols.index("sale_date")] <= "2026-08-15")
    assert mtd == pytest.approx(expected)
    assert payload["reporting_context"]["complete"] is True
    assert set(payload["comparisons"]) == {"DTD", "MTD", "PMTD", "MoM", "YTD", "YoY"}


async def test_comparison_periods_end_on_matching_day(service: TemporalMISService) -> None:
    payload = await service.performance("2026-08-15", "historical", "region")
    assert payload["comparisons"]["MTD"]["current_period"] == {
        "from": "2026-08-01", "to": "2026-08-15"}
    assert payload["comparisons"]["PMTD"]["reference_period"] == {
        "from": "2026-07-01", "to": "2026-07-15"}
    assert payload["comparisons"]["MoM"]["current_period"] == {
        "from": "2026-07-01", "to": "2026-07-31"}


async def test_unknown_snapshot_is_rejected(service: TemporalMISService) -> None:
    with pytest.raises(SnapshotNotFound):
        await service.performance("2026-08-16", "historical", "branch")


async def test_scd2_history_and_dual_attribution(service: TemporalMISService) -> None:
    hierarchy = await service.hierarchy("2026-08-15", "current")
    assert any(change["advisor_code"] == "P0037" and
               change["effective_date"] == "2026-08-08"
               for change in hierarchy["changes"])
    historical = await service.performance("2026-08-15", "historical", "branch")
    current = await service.performance("2026-08-15", "current", "branch")
    # Attribution may redistribute entities, but never changes the network total.
    h_total = next(k["value"] for k in historical["kpis"] if k["key"] == "mtd")
    c_total = next(k["value"] for k in current["kpis"] if k["key"] == "mtd")
    assert h_total == c_total


async def test_hierarchy_excludes_future_hires(dataset, service: TemporalMISService) -> None:
    selected = "2024-01-01"
    hierarchy = await service.hierarchy(selected, "historical")
    advisor_codes = {
        advisor["code"]
        for region in hierarchy["regions"]
        for branch in region["branches"]
        for advisor in branch["advisors"]
    }
    acols, arows = dataset["MIS_DIM_ADVISOR"]
    expected = {
        row[acols.index("advisor_code")] for row in arows
        if str(row[acols.index("hire_date")])[:10] <= selected
    }
    assert advisor_codes == expected


async def test_quality_gate_and_league_rules(service: TemporalMISService) -> None:
    quality = await service.quality("2026-08-15", "historical")
    assert quality["overall_status"] == "PASS"
    assert all(check["status"] == "PASS" for check in quality["checks"])
    league = await service.league("2026-08-15", "current", "advisor")
    assert league["rules"]["attainment_cap"] == 120
    assert league["rules"]["minimum_quality"] == 80
    assert league["columns"][0]["key"] == "rank"


async def test_scoped_plan_denominator_matches_analytic_rows(
        service: TemporalMISService) -> None:
    manager = SessionUser("BM_BEL01", "Branch manager", "BRANCH_MANAGER",
                          branch_id=1, region_id=1)
    payload = await service.performance("2026-08-15", "historical", "branch",
                                       manager)
    kpis = {item["key"]: item["value"] for item in payload["kpis"]}
    analytic_plan = sum(row[4] for row in payload["rows"])
    assert kpis["plan_mtd"] == pytest.approx(analytic_plan)


async def test_personal_view_stops_at_as_of(dataset) -> None:
    people = PeopleService(FakeRepository(dataset), ttl=60)
    payload = await people.cumulative("branch", "BEL01", "2026-08", None,
                                      "2026-08-15")
    assert len(payload["rows"]) == 15
    assert payload["rows"][-1][0] == "2026-08-15"
    assert payload["reporting_context"]["plan_pacing"] == "business_days"


async def test_campaign_results_respect_partial_snapshot(dataset) -> None:
    repository = AsOfMISRepository(FakeRepository(dataset), "2024-01-15")
    cols, rows = await repository.get_table("MIS_FACT_CAMPAIGN_RESULTS")
    assert not rows
