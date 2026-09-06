"""JWT and fake LDAP authentication tests over the in-memory MIS dataset."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import jwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.roles import SessionUser  # noqa: E402
from app.services.auth_service import (AuthService, InvalidCredentials,
                                       InvalidToken, PersonaSwitchForbidden,
                                       UnknownPersona)  # noqa: E402
from app.services.fake_ldap import FakeLDAPService  # noqa: E402
from app.services.session_service import SessionService  # noqa: E402
from fakes import FakeRepository  # noqa: E402
from seed import build_dataset  # noqa: E402


DEMO_CREDENTIALS = {
    "mis.sql": ("Demo-MIS-SQL-2026!", "MIS_SQL_DEVELOPER"),
    "network.head": ("Demo-Network-2026!", "NETWORK_HEAD"),
    "regional.director": ("Demo-Region-2026!", "REGIONAL_DIRECTOR"),
    "branch.director": ("Demo-Branch-2026!", "BRANCH_DIRECTOR"),
    "customer.advisor": ("Demo-Advisor-2026!", "CUSTOMER_ADVISOR"),
    "hq.full": ("Demo-HQ-2026!", "HQ_FULL_ACCESS"),
    "app.tester": ("Demo-Tester-2026!", "APP_TESTER"),
}


@pytest.fixture()
def auth() -> AuthService:
    repository = FakeRepository(build_dataset(scale=1))
    session = SessionService(repository)
    return AuthService(session, FakeLDAPService(), "unit-test-secret", ttl_minutes=60)


@pytest.mark.parametrize("username, expected", [
    (username, expected_role) for username, (_password, expected_role)
    in DEMO_CREDENTIALS.items()
])
async def test_demo_accounts_login_and_round_trip(
        auth: AuthService, username: str, expected: str) -> None:
    password = DEMO_CREDENTIALS[username][0]
    result = await auth.login(username, password)

    assert result.user.role == expected
    assert result.user.authenticated_username == username
    assert (await auth.authenticate(result.token)) == result.user


async def test_invalid_credentials_are_generic(auth: AuthService) -> None:
    with pytest.raises(InvalidCredentials, match="Invalid username or password"):
        await auth.login("missing", "wrong")
    with pytest.raises(InvalidCredentials, match="Invalid username or password"):
        await auth.login("mis.sql", "wrong")


async def test_tester_switches_persona_without_losing_actor(auth: AuthService) -> None:
    logged_in = await auth.login("app.tester", "Demo-Tester-2026!")
    switched = await auth.switch_persona(logged_in.user, "ADV_DEMO_P0001")

    assert switched.user.role == "CUSTOMER_ADVISOR"
    assert switched.user.code == "ADV_DEMO_P0001"
    assert switched.user.authenticated_username == "app.tester"
    assert switched.user.authenticated_user_code == "APP_TESTER01"
    assert switched.user.is_impersonating
    assert (await auth.authenticate(switched.token)) == switched.user


async def test_non_tester_cannot_switch(auth: AuthService) -> None:
    logged_in = await auth.login("branch.director", "Demo-Branch-2026!")
    with pytest.raises(PersonaSwitchForbidden):
        await auth.switch_persona(logged_in.user, "NET_HEAD01")


async def test_switch_rejects_unknown_persona(auth: AuthService) -> None:
    logged_in = await auth.login("app.tester", "Demo-Tester-2026!")
    with pytest.raises(UnknownPersona):
        await auth.switch_persona(logged_in.user, "DOES_NOT_EXIST")


async def test_tampered_and_expired_tokens_are_rejected(auth: AuthService) -> None:
    logged_in = await auth.login("mis.sql", "Demo-MIS-SQL-2026!")
    token_parts = logged_in.token.split(".")
    token_parts[2] = ("A" if token_parts[2][0] != "A" else "B") + token_parts[2][1:]
    with pytest.raises(InvalidToken):
        await auth.authenticate(".".join(token_parts))

    now = dt.datetime.now(dt.timezone.utc)
    expired = jwt.encode({
        "iss": "mis-dashboard",
        "sub": "mis.sql",
        "actor_username": "mis.sql",
        "actor_user_code": "MIS_SQL_DEV01",
        "effective_user_code": "MIS_SQL_DEV01",
        "iat": int((now - dt.timedelta(hours=2)).timestamp()),
        "exp": int((now - dt.timedelta(hours=1)).timestamp()),
    }, "unit-test-secret", algorithm="HS256")
    with pytest.raises(InvalidToken):
        await auth.authenticate(expired)


def test_role_capabilities_are_explicit() -> None:
    assert SessionUser("sql", "SQL", "MIS_SQL_DEVELOPER").can_refresh_cache
    assert SessionUser("hq", "HQ", "HQ_FULL_ACCESS").can_view_global_quality
    assert not SessionUser("branch", "Branch", "BRANCH_DIRECTOR",
                           branch_id=1).can_view_global_quality
