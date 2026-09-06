"""HTTP-layer tests for login cookies and tester-only persona switching."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.deps import get_current_user  # noqa: E402
from app.api.routes.auth import login, logout  # noqa: E402
from app.api.routes.session import switch_user, users  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.schemas.session import LoginRequest, SwitchUserRequest  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.session_service import SessionService  # noqa: E402
from fakes import FakeRepository  # noqa: E402
from seed import build_dataset  # noqa: E402
from app.services.fake_ldap import FakeLDAPService  # noqa: E402


@pytest.fixture()
def auth_and_settings() -> tuple[AuthService, Settings]:
    repository = FakeRepository(build_dataset(scale=1))
    session = SessionService(repository)
    settings = Settings(jwt_secret="api-test-secret", auth_cookie_secure=False)
    auth = AuthService(session, FakeLDAPService(), settings.jwt_secret,
                       settings.jwt_ttl_minutes, settings.auth_issuer)
    return auth, settings


def request_for(settings: Settings, auth: AuthService,
                token: str | None = None) -> Request:
    headers = []
    if token:
        headers.append((b"cookie", f"{settings.auth_cookie_name}={token}".encode()))
    app = SimpleNamespace(state=SimpleNamespace(settings=settings,
                                                auth_service=auth))
    return Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": headers, "query_string": b"", "app": app,
    })


async def test_login_sets_http_only_cookie(
        auth_and_settings: tuple[AuthService, Settings]) -> None:
    auth, settings = auth_and_settings
    response = Response()
    result = await login(
        LoginRequest(username="mis.sql", password="Demo-MIS-SQL-2026!"),
        response, request_for(settings, auth), auth,
    )

    assert result["user"]["role"] == "MIS_SQL_DEVELOPER"
    cookie = response.headers["set-cookie"]
    assert f"{settings.auth_cookie_name}=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie


async def test_cookie_auth_and_logout(
        auth_and_settings: tuple[AuthService, Settings]) -> None:
    auth, settings = auth_and_settings
    logged_in = await auth.login("hq.full", "Demo-HQ-2026!")
    user = await get_current_user(request_for(settings, auth, logged_in.token), auth)
    assert user.code == "HQ_FULL01"

    response = Response()
    await logout(response, request_for(settings, auth, logged_in.token))
    assert f'{settings.auth_cookie_name}=""' in response.headers["set-cookie"]


async def test_missing_cookie_is_401(
        auth_and_settings: tuple[AuthService, Settings]) -> None:
    auth, settings = auth_and_settings
    with pytest.raises(HTTPException) as caught:
        await get_current_user(request_for(settings, auth), auth)
    assert caught.value.status_code == 401


async def test_tester_switch_sets_replacement_cookie(
        auth_and_settings: tuple[AuthService, Settings]) -> None:
    auth, settings = auth_and_settings
    logged_in = await auth.login("app.tester", "Demo-Tester-2026!")
    current = await get_current_user(request_for(settings, auth, logged_in.token), auth)
    response = await switch_user(
        SwitchUserRequest(code="REG_DIR_RNOR"),
        request_for(settings, auth, logged_in.token), current, auth,
    )

    payload = json.loads(response.body)
    assert payload["user"]["role"] == "REGIONAL_DIRECTOR"
    assert payload["user"]["authenticated_username"] == "app.tester"
    assert f"{settings.auth_cookie_name}=" in response.headers["set-cookie"]


async def test_persona_directory_is_tester_only(
        auth_and_settings: tuple[AuthService, Settings]) -> None:
    auth, settings = auth_and_settings
    logged_in = await auth.login("network.head", "Demo-Network-2026!")
    current = await get_current_user(request_for(settings, auth, logged_in.token), auth)
    with pytest.raises(HTTPException) as caught:
        await users(current, SessionService(FakeRepository(build_dataset(scale=1))))
    assert caught.value.status_code == 403
