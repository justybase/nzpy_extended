"""FastAPI endpoints for point-in-time performance and MIS governance."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user, get_temporal_service
from app.core.roles import SessionUser
from app.schemas.temporal import (HierarchyResponse, LeagueResponse,
                                  PerformanceResponse, QualityResponse)
from app.services.temporal_service import SnapshotNotFound, TemporalMISService

router = APIRouter(prefix="/api", tags=["point-in-time MIS"],
                   dependencies=[Depends(get_current_user)])


def _error(exc: Exception) -> HTTPException:
    return HTTPException(422, str(exc))


@router.get("/performance", response_model=PerformanceResponse)
async def performance(
    as_of: str | None = Query(default=None),
    attribution: str = Query(default="historical"),
    dim: str = Query(default="branch"),
    service: TemporalMISService = Depends(get_temporal_service),
    user: SessionUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return await service.performance(as_of, attribution, dim, user)
    except (ValueError, SnapshotNotFound) as exc:
        raise _error(exc) from None

@router.get("/hierarchy", response_model=HierarchyResponse)
async def hierarchy(
    as_of: str | None = Query(default=None),
    attribution: str = Query(default="current"),
    service: TemporalMISService = Depends(get_temporal_service),
    user: SessionUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return await service.hierarchy(as_of, attribution, user)
    except (ValueError, SnapshotNotFound) as exc:
        raise _error(exc) from None


@router.get("/league", response_model=LeagueResponse)
async def league(
    as_of: str | None = Query(default=None),
    attribution: str = Query(default="current"),
    level: str = Query(default="advisor"),
    service: TemporalMISService = Depends(get_temporal_service),
    user: SessionUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return await service.league(as_of, attribution, level, user)
    except (ValueError, SnapshotNotFound) as exc:
        raise _error(exc) from None


@router.get("/quality", response_model=QualityResponse)
async def quality(
    as_of: str | None = Query(default=None),
    attribution: str = Query(default="historical"),
    service: TemporalMISService = Depends(get_temporal_service),
    user: SessionUser = Depends(get_current_user),
) -> dict[str, Any]:
    if not user.can_view_global_quality:
        raise HTTPException(403, "Global data quality is restricted to central roles")
    try:
        return await service.quality(as_of, attribution)
    except (ValueError, SnapshotNotFound) as exc:
        raise _error(exc) from None
