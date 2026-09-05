"""Session API — simulated sign-in: who am I, switch user, user directory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_session_service
from app.schemas.session import (SessionResponse, SwitchUserRequest,
                                 UsersResponse)
from app.services.session_service import SessionService, UnknownUser

router = APIRouter(prefix="/api/session", tags=["session"])


@router.get("/me", response_model=SessionResponse)
async def me(session: SessionService = Depends(get_session_service)) -> dict[str, Any]:
    return {"user": (await session.current()).to_dict()}


@router.post("/user", response_model=SessionResponse)
async def switch_user(body: SwitchUserRequest,
                      session: SessionService = Depends(get_session_service)) -> dict[str, Any]:
    try:
        user = await session.switch(body.code)
    except UnknownUser as exc:
        raise HTTPException(404, str(exc)) from None
    return {"user": user.to_dict()}


@router.get("/users", response_model=UsersResponse)
async def users(session: SessionService = Depends(get_session_service)) -> dict[str, Any]:
    return {"users": await session.users()}