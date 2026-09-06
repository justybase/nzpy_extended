"""Small HTTP-layer checks using dependency overrides; no Netezza required."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_ROOT))

from tests.fakes import FakeRepository  # noqa: E402
from seed import build_dataset  # noqa: E402
from app.api.routes.cockpit import cockpit, drivers  # noqa: E402
from app.api.routes.status import refresh  # noqa: E402
from app.services.cockpit_service import CockpitService  # noqa: E402
from app.api.routes.meta import meta  # noqa: E402
from app.api.routes.exports import _remove_export  # noqa: E402
from app.services.cockpit_service import DataNotReady  # noqa: E402


@pytest.fixture
def service() -> CockpitService:
    return CockpitService(FakeRepository(build_dataset(scale=0.25)), ttl=60)


async def test_meta_and_cockpit_contract(service: CockpitService) -> None:
    meta_payload = await meta(service=service)
    assert meta_payload["latest_week"]
    cockpit_payload = await cockpit(
        week=None, lookback=12, scope="network", region=None, unit=None,
        metric="sales_amount", service=service,
    )
    assert cockpit_payload["exceptions"]


async def test_meta_returns_503_when_data_is_not_ready() -> None:
    class NotReadyService:
        async def meta(self):
            raise DataNotReady("run: python seed.py")

    with pytest.raises(HTTPException) as error:
        await meta(service=NotReadyService())
    assert error.value.status_code == 503


def test_export_cleanup_removes_its_temporary_file() -> None:
    descriptor, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(descriptor)
    Path(path).write_bytes(b"test export")
    try:
        _remove_export(path)
        assert not Path(path).exists()
    finally:
        Path(path).unlink(missing_ok=True)


async def test_route_validation_errors_are_translated(service: CockpitService) -> None:
    with pytest.raises(Exception) as unit_error:
        await cockpit(
            week=None, lookback=12, scope="unit", region=None, unit=None,
            metric="sales_amount", service=service,
        )
    assert getattr(unit_error.value, "status_code", None) == 422
    with pytest.raises(Exception) as dimension_error:
        await drivers(
            week=None, lookback=12, scope="network", region=None, unit=None,
            metric="sales_amount", dimension="nope", service=service,
        )
    assert getattr(dimension_error.value, "status_code", None) == 422


async def test_refresh_clears_derived_service_cache(service: CockpitService) -> None:
    repository = service._repository
    await service.meta()
    assert repository.load_count == 10
    result = await refresh(repository=repository, service=service)
    assert result["committed"] is True
    await service.meta()
    assert repository.load_count == 20
