"""Resolve authenticated MIS users and role metadata.

Authentication is handled by :mod:`app.services.auth_service`. This service
only resolves the effective persona from ``MIS_DIM_USER``; it deliberately
keeps no mutable current-user state, so concurrent browser sessions are
independent.
"""

from __future__ import annotations

from typing import Any

from app.core.roles import DEFAULT_USER_CODE, ROLE_LABELS, ROLE_ORDER, SessionUser
from app.repositories.base import MISRepository


class UnknownUser(Exception):
    pass


class SessionService:
    def __init__(self, repository: MISRepository) -> None:
        self._repository = repository

    async def _user_row(self, code: str) -> list[Any] | None:
        cols, rows = await self._repository.get_table("MIS_DIM_USER")
        ci = cols.index("user_code")
        return next((r for r in rows if r[ci] == code), None)

    @staticmethod
    def _to_user(row: list[Any], cols: list[str],
                 authenticated_username: str | None = None,
                 authenticated_user_code: str | None = None,
                 can_switch_persona: bool = False) -> SessionUser:
        return SessionUser(
            code=row[cols.index("user_code")],
            name=row[cols.index("display_name")],
            role=row[cols.index("role")],
            advisor_id=row[cols.index("advisor_id")],
            branch_id=row[cols.index("branch_id")],
            region_id=row[cols.index("region_id")],
            authenticated_username=authenticated_username,
            authenticated_user_code=authenticated_user_code,
            can_switch_persona=can_switch_persona,
        )

    async def current(self, effective_user_code: str | None = None,
                      authenticated_username: str | None = None,
                      authenticated_user_code: str | None = None,
                      can_switch_persona: bool = False) -> SessionUser:
        """Resolve the requested persona, retaining the old test fallback."""
        code = effective_user_code or DEFAULT_USER_CODE
        row = await self._user_row(code)
        if row is None:
            if effective_user_code is not None:
                raise UnknownUser(f"Unknown user: {effective_user_code}")
            return SessionUser(code=DEFAULT_USER_CODE, name="Network Analyst",
                               role="ANALYST")
        cols, _ = await self._repository.get_table("MIS_DIM_USER")
        return self._to_user(row, cols, authenticated_username,
                             authenticated_user_code, can_switch_persona)

    async def resolve(self, code: str, authenticated_username: str | None = None,
                      authenticated_user_code: str | None = None,
                      can_switch_persona: bool = False) -> SessionUser:
        row = await self._user_row(code)
        if row is None:
            raise UnknownUser(f"Unknown user: {code}")
        cols, _ = await self._repository.get_table("MIS_DIM_USER")
        return self._to_user(row, cols, authenticated_username,
                             authenticated_user_code, can_switch_persona)

    async def switch(self, code: str) -> SessionUser:
        """Compatibility helper; it no longer changes global process state."""
        return await self.resolve(code)

    async def users(self) -> list[dict[str, Any]]:
        cols, rows = await self._repository.get_table("MIS_DIM_USER")
        out: list[dict[str, Any]] = []
        for r in rows:
            role = r[cols.index("role")]
            out.append({
                "code": r[cols.index("user_code")],
                "name": r[cols.index("display_name")],
                "role": role,
                "role_label": ROLE_LABELS.get(role, role),
            })
        out.sort(key=lambda u: (ROLE_ORDER.index(u["role"])
                                if u["role"] in ROLE_ORDER else 99,
                                u["name"]))
        return out
