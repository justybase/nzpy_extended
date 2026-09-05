"""Role model for the simulated session — shared by services and repositories.

Kept in core so the repository layer (ScopedMISRepository) can depend on the
role model without importing from services.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ROLE_LABELS = {"ANALYST": "Network analyst", "AREA_MANAGER": "Area manager",
               "BRANCH_MANAGER": "Branch manager", "ADVISOR": "Advisor"}
ROLE_ORDER = ["ANALYST", "AREA_MANAGER", "BRANCH_MANAGER", "ADVISOR"]

DEFAULT_USER_CODE = "NET01"


@dataclass(frozen=True)
class SessionUser:
    code: str
    name: str
    role: str
    advisor_id: int | None = None
    branch_id: int | None = None
    region_id: int | None = None

    @property
    def is_analyst(self) -> bool:
        return self.role == "ANALYST"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "role": self.role,
            "role_label": ROLE_LABELS.get(self.role, self.role),
            "advisor_id": self.advisor_id,
            "branch_id": self.branch_id,
            "region_id": self.region_id,
        }