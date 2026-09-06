"""Metadata used to populate the report toolbar."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_cockpit_service
from app.schemas.cockpit import MetaResponse
from app.services.cockpit_service import CockpitService, DataNotReady

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta", response_model=MetaResponse)
async def meta(service: CockpitService = Depends(get_cockpit_service)) -> dict[str, Any]:
    try:
        return await service.meta()
    except DataNotReady as exc:
        raise HTTPException(503, str(exc)) from None
