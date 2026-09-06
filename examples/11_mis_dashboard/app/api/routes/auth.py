"""Public login/logout endpoints for the dashboard's demo identity provider."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.deps import get_auth_service, get_current_user
from app.core.config import Settings
from app.core.roles import SessionUser
from app.schemas.session import LoginRequest, SessionResponse
from app.services.auth_service import AuthService, InvalidCredentials, InvalidToken

router = APIRouter(prefix="/api/auth", tags=["authentication"])


def _set_access_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.jwt_ttl_minutes * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/login", response_model=SessionResponse)
async def login(body: LoginRequest, response: Response,
                request: Request,
                auth_service: AuthService = Depends(get_auth_service)) -> dict[str, Any]:
    try:
        result = await auth_service.login(body.username, body.password)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    except InvalidToken as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    _set_access_cookie(response, request.app.state.settings, result.token)
    return {"user": result.user.to_dict()}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=request.app.state.settings.auth_cookie_name,
        path="/",
    )


@router.get("/me", response_model=SessionResponse)
async def me(user: SessionUser = Depends(get_current_user)) -> dict[str, Any]:
    return {"user": user.to_dict()}
