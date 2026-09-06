"""Settings for the Sales Decision Cockpit example."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


TABLE_NAMES = (
    "SDC_DIM_DATE",
    "SDC_DIM_REGION",
    "SDC_DIM_UNIT",
    "SDC_DIM_SELLER",
    "SDC_DIM_PRODUCT",
    "SDC_DIM_CHANNEL",
    "SDC_FACT_WEEKLY_SCORECARD",
    "SDC_FACT_WEEKLY_TARGET",
    "SDC_AUDIT_DATASET_LOAD",
    "SDC_CONTROL_DATASET_LOAD",
)


@dataclass(frozen=True)
class Settings:
    nz_host: str = ""
    nz_port: int = 0
    nz_database: str = ""
    nz_user: str = ""
    nz_password: str = ""
    nz_min_pool: int = 1
    nz_max_pool: int = 4

    cache_ttl_tables: int = 86_400
    cache_ttl_reports: int = 120
    dataset_name: str = "SALES_DECISION_COCKPIT"
    control_table: str = "SDC_CONTROL_DATASET_LOAD"

    static_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[2] / "static"
    )
    host: str = "0.0.0.0"
    port: int = 8482
    table_names: tuple[str, ...] = TABLE_NAMES

    @classmethod
    def from_env(cls) -> "Settings":
        required = (
            "NZ_DEV_HOST",
            "NZ_DEV_PORT",
            "NZ_DEV_DATABASE",
            "NZ_DEV_USER",
            "NZ_DEV_PASSWORD",
        )
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError(
                "Missing required database settings: "
                + ", ".join(missing)
                + ". Set them explicitly before running the cockpit."
            )
        try:
            nz_port = int(os.environ["NZ_DEV_PORT"])
        except ValueError as exc:
            raise RuntimeError("NZ_DEV_PORT must be an integer") from exc
        if nz_port <= 0:
            raise RuntimeError("NZ_DEV_PORT must be a positive integer")
        return cls(
            nz_host=os.environ["NZ_DEV_HOST"],
            nz_port=nz_port,
            nz_database=os.environ["NZ_DEV_DATABASE"],
            nz_user=os.environ["NZ_DEV_USER"],
            nz_password=os.environ["NZ_DEV_PASSWORD"],
            nz_min_pool=int(os.environ.get("NZ_MIN_POOL", "1")),
            nz_max_pool=int(os.environ.get("NZ_MAX_POOL", "4")),
            cache_ttl_tables=int(os.environ.get("SDC_CACHE_TTL_TABLES", "86400")),
            cache_ttl_reports=int(os.environ.get("SDC_CACHE_TTL_REPORTS", "120")),
            dataset_name=os.environ.get(
                "SDC_DATASET_NAME", "SALES_DECISION_COCKPIT"
            ),
            control_table=os.environ.get(
                "SDC_CONTROL_TABLE", "SDC_CONTROL_DATASET_LOAD"
            ),
            host=os.environ.get("SDC_HOST", "0.0.0.0"),
            port=int(os.environ.get("SDC_PORT", "8482")),
        )

    def require_connection(self) -> None:
        values = {
            "NZ_DEV_HOST": self.nz_host,
            "NZ_DEV_PORT": self.nz_port,
            "NZ_DEV_DATABASE": self.nz_database,
            "NZ_DEV_USER": self.nz_user,
            "NZ_DEV_PASSWORD": self.nz_password,
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError(
                "Missing required database settings: "
                + ", ".join(missing)
                + ". Set them explicitly before starting the cockpit."
            )
        if self.nz_port <= 0:
            raise RuntimeError("NZ_DEV_PORT must be a positive integer")
