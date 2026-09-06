"""Database-free tests for the exception-first reporting logic."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from nzpy_extended import ProgrammingError

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_ROOT))

from tests.fakes import FakeRepository  # noqa: E402
from seed import LATEST_WEEK, build_dataset  # noqa: E402
from app.services.cockpit_service import CockpitService  # noqa: E402
from app.services.export_service import ExportService  # noqa: E402
from app.services.cockpit_service import DataNotReady  # noqa: E402


@pytest.fixture
def service() -> CockpitService:
    return CockpitService(FakeRepository(build_dataset(scale=0.25)), ttl=60)


async def test_cockpit_is_exception_first_and_contains_late_period_signal(service: CockpitService) -> None:
    payload = await service.cockpit()
    assert payload["week"] == LATEST_WEEK.isoformat()
    assert payload["exceptions"]
    assert set(payload["status_counts"]) == {"critical", "warning", "good", "neutral"}
    assert any(row["severity"] == "critical" for row in payload["exceptions"])
    assert payload["narrative"][0]["kind"] == "Decision"
    assert payload["charts"][0]["id"] == "trend"


async def test_cockpit_cache_avoids_reloading_tables(service: CockpitService) -> None:
    repo = service._repository  # test the explicit service cache boundary
    await service.cockpit()
    first = repo.load_count
    await service.cockpit()
    assert repo.load_count == first


async def test_unit_scope_requires_unit(service: CockpitService) -> None:
    with pytest.raises(ValueError, match="unit is required"):
        await service.cockpit(scope="unit")


async def test_region_scope_groups_by_region_not_unit() -> None:
    regional_service = CockpitService(FakeRepository(build_dataset(scale=0.5)), ttl=60)
    payload = await regional_service.cockpit(scope="region")
    assert len(payload["exceptions"]) == 2
    assert {row["code"] for row in payload["exceptions"]} == {"NORTH", "WEST"}
    assert {row["parent"] for row in payload["exceptions"]} == {"Network"}


async def test_missing_audit_is_not_reconciled() -> None:
    tables = build_dataset(scale=0.25)
    columns, rows = tables["SDC_AUDIT_DATASET_LOAD"]
    tables["SDC_AUDIT_DATASET_LOAD"] = (
        columns,
        [row for row in rows if str(row[0])[:10] != LATEST_WEEK.isoformat()],
    )
    payload = await CockpitService(FakeRepository(tables), ttl=60).cockpit()
    assert payload["freshness"]["status"] == "MISSING_AUDIT"
    assert payload["freshness"]["reconciled"] is False


async def test_driver_bridge_reconciles_to_scope_gap(service: CockpitService) -> None:
    payload = await service.drivers(dimension="product")
    contribution_sum = sum(row["contribution"] for row in payload["drivers"])
    assert contribution_sum == pytest.approx(payload["total_gap"], abs=0.02)
    assert payload["bridge"][0]["kind"] == "base"
    assert payload["bridge"][-1]["kind"] == "total"


async def test_driver_dimensions_and_filtering(service: CockpitService) -> None:
    payload = await service.drivers(region="NORTH", dimension="channel")
    assert payload["dimension"] == "channel"
    assert payload["drivers"]
    assert all(row["label"] for row in payload["drivers"])


async def test_driver_scope_contract_matches_cockpit(service: CockpitService) -> None:
    with pytest.raises(ValueError, match="unit is required"):
        await service.drivers(scope="unit")
    with pytest.raises(ValueError, match="scope must be"):
        await service.drivers(scope="seller")


async def test_driver_database_errors_are_actionable() -> None:
    class MissingTableRepository(FakeRepository):
        async def get_table(self, name: str):
            raise ProgrammingError("relation does not exist")

    with pytest.raises(DataNotReady, match="Missing table"):
        await CockpitService(MissingTableRepository({}), ttl=60).meta()


async def test_review_pack_is_deterministic_and_read_only(service: CockpitService) -> None:
    first = await service.review_pack()
    second = await service.review_pack()
    assert first["decision"] == second["decision"]
    assert first["recommended_actions"] == second["recommended_actions"]
    assert first["recommended_actions"][0]["due"] == "2026-08-31"


async def test_invalid_period_and_metric_are_rejected(service: CockpitService) -> None:
    with pytest.raises(ValueError, match="Unknown review week"):
        await service.cockpit(week="2030-01-01")
    with pytest.raises(ValueError, match="metric must be one of"):
        await service.cockpit(metric="profit")
    with pytest.raises(ValueError, match="lookback must be between"):
        await service.cockpit(lookback=2)


async def test_exports_roundtrip(service: CockpitService) -> None:
    from xlspy import ExcelReader

    exporter = ExportService(service)
    for fmt in ("xlsx", "xlsb"):
        result = await exporter.export_drivers(fmt, dimension="product")
        try:
            with ExcelReader(result.path) as reader:
                names = reader.get_sheet_names()
                rows = reader.read_all(names[0])
            assert names[-1] == "Report info"
            assert rows[0][:4] == ["Code", "Driver", "Actual", "Target"]
            assert len(rows) > 1
        finally:
            Path(result.path).unlink(missing_ok=True)
