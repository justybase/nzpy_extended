"""Authenticated session API and tester-only persona switching."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.deps import get_auth_service, get_current_user, get_session_service
from app.core.roles import SessionUser
from app.schemas.session import (SessionResponse, SwitchUserRequest,
                                 UsersResponse)
from app.services.auth_service import (AuthService, PersonaSwitchForbidden,
                                       UnknownPersona)
from app.services.session_service import SessionService

router = APIRouter(prefix="/api/session", tags=["session"])


@router.get("/me", response_model=SessionResponse)
async def me(user: SessionUser = Depends(get_current_user)) -> dict[str, Any]:
    # Keep this route as the compatibility alias used by the existing UI.
    return {"user": user.to_dict()}


@router.post("/user", response_model=SessionResponse)
async def switch_user(body: SwitchUserRequest,
                      request: Request,
                      user: SessionUser = Depends(get_current_user),
                      auth_service: AuthService = Depends(get_auth_service)) -> JSONResponse:
    try:
        result = await auth_service.switch_persona(user, body.code)
    except PersonaSwitchForbidden as exc:
        raise HTTPException(403, str(exc)) from None
    except UnknownPersona as exc:
        raise HTTPException(404, str(exc)) from None
    response = JSONResponse({"user": result.user.to_dict()})
    settings = request.app.state.settings
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=result.token,
        max_age=settings.jwt_ttl_minutes * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/users", response_model=UsersResponse)
async def users(user: SessionUser = Depends(get_current_user),
                session: SessionService = Depends(get_session_service)) -> dict[str, Any]:
    if not user.can_switch_persona:
        raise HTTPException(403, "Only the application tester may list switchable personas")
    return {"users": await session.users()}
