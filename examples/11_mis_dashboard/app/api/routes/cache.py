"""POST /api/cache/refresh — reload all tables from Netezza."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_cache_coordinator, get_current_user
from app.core.roles import SessionUser
from app.services.cache_coordinator import CacheCoordinator

router = APIRouter(prefix="/api/cache", tags=["cache"],
                   dependencies=[Depends(get_current_user)])


@router.post("/refresh")
async def refresh(coordinator: CacheCoordinator = Depends(get_cache_coordinator),
                  user: SessionUser = Depends(get_current_user)) -> dict[str, Any]:
    if not user.can_refresh_cache:
        raise HTTPException(403, "Your role cannot refresh the data cache")
    result = await coordinator.refresh("manual")
    result["refresh_reason"] = "manual"
    return result
