"""
Application settings, read from the environment.

Connection uses the standard NZ_DEV_* variables (same convention as the
driver tests and examples/10_query_app). Caching behaviour:

    NZ_CACHE_TTL_TABLES       how long complete Netezza tables stay in memory (s)
    NZ_CACHE_TTL_REPORTS      how long computed report payloads stay cached (s)
    NZ_CACHE_REFRESH_SECONDS  background warm-refresh interval for the tables (s)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # connection
    nz_host: str = "192.168.0.144"
    nz_port: int = 5480
    nz_database: str = "JUST_DATA"
    nz_user: str = "admin"
    nz_password: str = "password"
    nz_min_pool: int = 1
    nz_max_pool: int = 4

    # caching
    cache_ttl_tables: int = 900          # 15 min
    cache_ttl_reports: int = 120         # 2 min
    cache_refresh_seconds: int = 300     # 5 min

    # app
    static_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2] / "static")
    host: str = "0.0.0.0"
    port: int = 8481

    table_names: tuple[str, ...] = (
        "MIS_DIM_REGION",
        "MIS_DIM_BRANCH",
        "MIS_DIM_ADVISOR",
        "MIS_DIM_PRODUCT",
        "MIS_DIM_CHANNEL",
        "MIS_DIM_CAMPAIGN",
        "MIS_FACT_SALES",
        "MIS_FACT_BALANCES",
        "MIS_FACT_CUSTOMER_MOVEMENT",
        "MIS_FACT_CAMPAIGN_RESULTS",
    )

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            nz_host=os.environ.get("NZ_DEV_HOST", "192.168.0.144"),
            nz_port=int(os.environ.get("NZ_DEV_PORT", "5480")),
            nz_database=os.environ.get("NZ_DEV_DATABASE") or os.environ.get("NZ_DEV_DB", "JUST_DATA"),
            nz_user=os.environ.get("NZ_DEV_USER", "admin"),
            nz_password=os.environ.get("NZ_DEV_PASSWORD", "password"),
            nz_min_pool=int(os.environ.get("NZ_MIN_POOL", "1")),
            nz_max_pool=int(os.environ.get("NZ_MAX_POOL", "4")),
            cache_ttl_tables=int(os.environ.get("NZ_CACHE_TTL_TABLES", "900")),
            cache_ttl_reports=int(os.environ.get("NZ_CACHE_TTL_REPORTS", "120")),
            cache_refresh_seconds=int(os.environ.get("NZ_CACHE_REFRESH_SECONDS", "300")),
            host=os.environ.get("NZ_DASH_HOST", "0.0.0.0"),
            port=int(os.environ.get("NZ_DASH_PORT", "8481")),
        )