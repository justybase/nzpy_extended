"""
SessionService — simulated authentication / authorization.

There is no real login in this example: a "signed in" user is picked from the
MIS_DIM_USER table and kept in memory (per app process). Every people view is
then scoped to what that role may see:

    ANALYST         - full network access
    AREA_MANAGER    - branches (and their advisors) within one region
    BRANCH_MANAGER  - one branch (and its advisors)
    ADVISOR         - themselves (and their own branch)

The scope is enforced in the service layer (PeopleService raises Forbidden),
not just hidden in the UI.
"""

from __future__ import annotations

from typing import Any

from app.core.roles import (DEFAULT_USER_CODE, ROLE_LABELS, ROLE_ORDER,  # noqa: F401
                            SessionUser)
from app.repositories.base import MISRepository


class UnknownUser(Exception):
    pass


class SessionService:
    def __init__(self, repository: MISRepository) -> None:
        self._repository = repository
        self._current_code: str | None = None  # None -> default (analyst)

    # -- user lookup ---------------------------------------------------------

    async def _user_row(self, code: str) -> list[Any] | None:
        cols, rows = await self._repository.get_table("MIS_DIM_USER")
        ci = cols.index("user_code")
        return next((r for r in rows if r[ci] == code), None)

    @staticmethod
    def _to_user(row: list[Any], cols: list[str]) -> SessionUser:
        return SessionUser(
            code=row[cols.index("user_code")],
            name=row[cols.index("display_name")],
            role=row[cols.index("role")],
            advisor_id=row[cols.index("advisor_id")],
            branch_id=row[cols.index("branch_id")],
            region_id=row[cols.index("region_id")],
        )

    # -- current session -----------------------------------------------------

    async def current(self) -> SessionUser:
        code = self._current_code or DEFAULT_USER_CODE
        row = await self._user_row(code)
        if row is None:
            # fallback: synthesize the analyst so the UI never breaks
            return SessionUser(code=DEFAULT_USER_CODE, name="Network Analyst",
                               role="ANALYST")
        cols, _ = await self._repository.get_table("MIS_DIM_USER")
        return self._to_user(row, cols)

    async def switch(self, code: str) -> SessionUser:
        row = await self._user_row(code)
        if row is None:
            raise UnknownUser(f"Unknown user: {code}")
        cols, _ = await self._repository.get_table("MIS_DIM_USER")
        self._current_code = code
        return self._to_user(row, cols)

    # -- user directory ------------------------------------------------------

    async def users(self) -> list[dict[str, Any]]:
        cols, rows = await self._repository.get_table("MIS_DIM_USER")
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({
                "code": r[cols.index("user_code")],
                "name": r[cols.index("display_name")],
                "role": r[cols.index("role")],
                "role_label": ROLE_LABELS.get(r[cols.index("role")], r[cols.index("role")]),
            })
        out.sort(key=lambda u: (ROLE_ORDER.index(u["role"]) if u["role"] in ROLE_ORDER
                                else 99, u["name"]))
        return out