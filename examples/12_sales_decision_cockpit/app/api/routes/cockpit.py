"""Decision cockpit and driver-analysis endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_cockpit_service
from app.schemas.cockpit import CockpitResponse, DriversResponse, ReviewPackResponse
from app.services.cockpit_service import CockpitService, DataNotReady

router = APIRouter(prefix="/api", tags=["decision reporting"])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, DataNotReady):
        return HTTPException(503, str(exc))
    return HTTPException(422, str(exc))


@router.get("/cockpit", response_model=CockpitResponse)
async def cockpit(
    week: str | None = Query(default=None),
    lookback: int = Query(default=12),
    scope: str = Query(default="network"),
    region: str | None = Query(default=None),
    unit: str | None = Query(default=None),
    metric: str = Query(default="sales_amount"),
    service: CockpitService = Depends(get_cockpit_service),
) -> dict[str, Any]:
    try:
        return await service.cockpit(week, lookback, scope, region, unit, metric)
    except (ValueError, DataNotReady) as exc:
        raise _error(exc) from None


@router.get("/drivers", response_model=DriversResponse)
async def drivers(
    week: str | None = Query(default=None),
    lookback: int = Query(default=12),
    scope: str = Query(default="network"),
    region: str | None = Query(default=None),
    unit: str | None = Query(default=None),
    metric: str = Query(default="sales_amount"),
    dimension: str = Query(default="product"),
    service: CockpitService = Depends(get_cockpit_service),
) -> dict[str, Any]:
    try:
        return await service.drivers(week, lookback, scope, region, unit, metric, dimension)
    except (ValueError, DataNotReady) as exc:
        raise _error(exc) from None


@router.get("/review-pack", response_model=ReviewPackResponse)
async def review_pack(
    week: str | None = Query(default=None),
    scope: str = Query(default="network"),
    region: str | None = Query(default=None),
    unit: str | None = Query(default=None),
    metric: str = Query(default="sales_amount"),
    service: CockpitService = Depends(get_cockpit_service),
) -> dict[str, Any]:
    try:
        return await service.review_pack(week, scope, region, unit, metric)
    except (ValueError, DataNotReady) as exc:
        raise _error(exc) from None
