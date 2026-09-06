"""JWT authentication orchestration for the dashboard demo."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, cast

import jwt

from app.core.roles import SessionUser
from app.services.fake_ldap import FakeLDAPService, LDAPAccount
from app.services.session_service import SessionService, UnknownUser


class InvalidCredentials(Exception):
    """The username/password pair is not valid."""


class InvalidToken(Exception):
    """The token is missing, expired, malformed or no longer resolvable."""


class PersonaSwitchForbidden(Exception):
    """The authenticated account is not allowed to switch personas."""


class UnknownPersona(Exception):
    """The requested effective MIS user does not exist."""


@dataclass(frozen=True)
class AuthResult:
    user: SessionUser
    token: str


class AuthService:
    def __init__(self, session: SessionService, ldap: FakeLDAPService,
                 secret: str, ttl_minutes: int = 60,
                 issuer: str = "mis-dashboard") -> None:
        if not secret:
            raise ValueError("MIS_JWT_SECRET must not be empty")
        if ttl_minutes <= 0:
            raise ValueError("MIS_JWT_TTL_MINUTES must be greater than zero")
        self._session = session
        self._ldap = ldap
        self._secret = secret
        self._ttl_minutes = ttl_minutes
        self._issuer = issuer

    async def login(self, username: str, password: str) -> AuthResult:
        account = self._ldap.authenticate(username, password)
        if account is None:
            raise InvalidCredentials("Invalid username or password")
        return await self._issue_for(account, account.user_code)

    async def authenticate(self, token: str) -> SessionUser:
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                options={
                    "require": [
                        "exp", "iat", "iss", "sub", "actor_username",
                        "actor_user_code", "effective_user_code",
                    ],
                },
            )
        except jwt.InvalidTokenError as exc:
            raise InvalidToken("Invalid or expired access token") from exc

        username = payload.get("actor_username")
        actor_code = payload.get("actor_user_code")
        effective_code = payload.get("effective_user_code")
        subject = payload.get("sub")
        if not all(isinstance(value, str) and value for value in
                   (username, actor_code, effective_code, subject)):
            raise InvalidToken("Invalid access token claims")
        if subject != username:
            raise InvalidToken("Invalid access token subject")

        username_value = cast(str, username)
        actor_code_value = cast(str, actor_code)
        effective_code_value = cast(str, effective_code)
        account = self._ldap.account(username_value)
        if (account is None or account.user_code != actor_code_value
                or (effective_code_value != account.user_code
                    and not account.can_switch_persona)):
            raise InvalidToken("Invalid access token identity")
        try:
            return await self._session.resolve(
                effective_code_value,
                authenticated_username=account.username,
                authenticated_user_code=account.user_code,
                can_switch_persona=account.can_switch_persona,
            )
        except UnknownUser as exc:
            raise InvalidToken("The token refers to an unavailable demo user") from exc

    async def switch_persona(self, current_user: SessionUser,
                             effective_code: str) -> AuthResult:
        username = current_user.authenticated_username
        account = self._ldap.account(username or "")
        if (account is None or not account.can_switch_persona
                or account.user_code != current_user.authenticated_user_code):
            raise PersonaSwitchForbidden("Only the application tester may switch personas")
        try:
            await self._session.resolve(effective_code)
        except UnknownUser as exc:
            raise UnknownPersona(f"Unknown user: {effective_code}") from exc
        return await self._issue_for(account, effective_code)

    async def _issue_for(self, account: LDAPAccount,
                         effective_code: str) -> AuthResult:
        try:
            user = await self._session.resolve(
                effective_code,
                authenticated_username=account.username,
                authenticated_user_code=account.user_code,
                can_switch_persona=account.can_switch_persona,
            )
        except UnknownUser as exc:
            raise InvalidToken("The configured demo user is unavailable") from exc
        now = dt.datetime.now(dt.timezone.utc)
        issued_at = int(now.timestamp())
        expires_at = int((now + dt.timedelta(minutes=self._ttl_minutes)).timestamp())
        payload = {
            "iss": self._issuer,
            "sub": account.username,
            "actor_username": account.username,
            "actor_user_code": account.user_code,
            "effective_user_code": effective_code,
            "iat": issued_at,
            "exp": expires_at,
        }
        token = jwt.encode(payload, self._secret, algorithm="HS256")
        return AuthResult(user=user, token=token)
