"""Read-only service status endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_cockpit_service, get_repository
from app.db.pool import pool_stats
from app.repositories.base import SDCRepository
from app.services.cockpit_service import CockpitService

router = APIRouter(prefix="/api", tags=["status"])


@router.post("/refresh")
async def refresh(
    repository: SDCRepository = Depends(get_repository),
    service: CockpitService = Depends(get_cockpit_service),
) -> dict[str, Any]:
    result = await repository.refresh_all()
    if not result.get("committed"):
        raise HTTPException(503, "; ".join(result.get("errors", [])) or "Cache refresh failed")
    service.clear_cache()
    return result


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    pool = request.app.state.pool
    return {
        "database": request.app.state.settings.nz_database,
        "host": request.app.state.settings.nz_host,
        "pool": pool_stats(pool),
        "cache": request.app.state.repository.snapshot(),
    }
