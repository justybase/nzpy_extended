"""Role and access model shared by services and repositories.

The dashboard has two kinds of role names.  The canonical names describe the
demo personas used by the login flow, while the shorter legacy names remain
valid for existing seeded users and examples.  Scope decisions live here so
every service applies the same policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ROLE_LABELS = {
    "MIS_SQL_DEVELOPER": "MIS SQL developer",
    "NETWORK_HEAD": "Head of network",
    "REGIONAL_DIRECTOR": "Regional director",
    "BRANCH_DIRECTOR": "Branch director",
    "CUSTOMER_ADVISOR": "Customer advisor",
    "HQ_FULL_ACCESS": "HQ full access",
    "APP_TESTER": "Application developer / tester",
    # Backwards-compatible role names already present in the demo dataset.
    "ANALYST": "Network analyst",
    "AREA_MANAGER": "Area manager",
    "BRANCH_MANAGER": "Branch manager",
    "ADVISOR": "Advisor",
}

ROLE_ORDER = [
    "MIS_SQL_DEVELOPER", "NETWORK_HEAD", "HQ_FULL_ACCESS", "APP_TESTER",
    "REGIONAL_DIRECTOR", "AREA_MANAGER", "BRANCH_DIRECTOR", "BRANCH_MANAGER",
    "CUSTOMER_ADVISOR", "ADVISOR",
]

FULL_SCOPE_ROLES = frozenset({
    "MIS_SQL_DEVELOPER", "NETWORK_HEAD", "HQ_FULL_ACCESS", "APP_TESTER",
    "ANALYST",
})
REGION_SCOPE_ROLES = frozenset({"REGIONAL_DIRECTOR", "AREA_MANAGER"})
BRANCH_SCOPE_ROLES = frozenset({"BRANCH_DIRECTOR", "BRANCH_MANAGER"})
ADVISOR_SCOPE_ROLES = frozenset({"CUSTOMER_ADVISOR", "ADVISOR"})
TECHNICAL_ROLES = frozenset({"MIS_SQL_DEVELOPER", "HQ_FULL_ACCESS", "APP_TESTER"})
GLOBAL_QUALITY_ROLES = frozenset({
    "MIS_SQL_DEVELOPER", "NETWORK_HEAD", "HQ_FULL_ACCESS", "APP_TESTER", "ANALYST",
})

DEFAULT_USER_CODE = "NET01"


@dataclass(frozen=True)
class SessionUser:
    code: str
    name: str
    role: str
    advisor_id: int | None = None
    branch_id: int | None = None
    region_id: int | None = None
    authenticated_username: str | None = None
    authenticated_user_code: str | None = None
    can_switch_persona: bool = False

    @property
    def has_full_scope(self) -> bool:
        return self.role in FULL_SCOPE_ROLES

    @property
    def is_region_scoped(self) -> bool:
        return self.role in REGION_SCOPE_ROLES

    @property
    def is_branch_scoped(self) -> bool:
        return self.role in BRANCH_SCOPE_ROLES

    @property
    def is_advisor_scoped(self) -> bool:
        return self.role in ADVISOR_SCOPE_ROLES

    @property
    def is_analyst(self) -> bool:
        """Compatibility alias used by the original report services."""
        return self.has_full_scope

    @property
    def is_impersonating(self) -> bool:
        return bool(self.authenticated_user_code
                    and self.code != self.authenticated_user_code)

    @property
    def can_refresh_cache(self) -> bool:
        return self.role in TECHNICAL_ROLES

    @property
    def can_view_global_quality(self) -> bool:
        return self.role in GLOBAL_QUALITY_ROLES

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "role": self.role,
            "role_label": ROLE_LABELS.get(self.role, self.role),
            "advisor_id": self.advisor_id,
            "branch_id": self.branch_id,
            "region_id": self.region_id,
            "authenticated_username": self.authenticated_username,
            "authenticated_user_code": self.authenticated_user_code,
            "is_impersonating": self.is_impersonating,
            "can_switch_persona": self.can_switch_persona,
            "can_refresh_cache": self.can_refresh_cache,
            "can_view_global_quality": self.can_view_global_quality,
        }
