"""
Application settings, read from the environment.

Connection uses the standard NZ_DEV_* variables (same convention as the
driver tests and examples/10_query_app). Caching behaviour:

    NZ_CACHE_TTL_TABLES              cache retention/status window (s)
    NZ_CACHE_TTL_REPORTS             how long computed report payloads stay cached (s)
    NZ_CACHE_CONTROL_POLL_SECONDS    polling interval for the ETL control table (s)
    NZ_CACHE_MAX_UNCONFIRMED_SECONDS warning threshold when freshness cannot be confirmed (s)
    NZ_CACHE_SQLITE_PATH             durable local snapshot path; empty disables it
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
    cache_ttl_tables: int = 86_400       # 24 h; refresh is ETL-version driven
    cache_ttl_reports: int = 120         # 2 min
    cache_control_poll_seconds: int = 300  # 5 min
    cache_max_unconfirmed_seconds: int = 86_400  # 24 h
    cache_dataset_name: str = "MIS_DASHBOARD"
    cache_control_table: str = "MIS_CONTROL_DATASET_LOAD"
    cache_sqlite_path: Path | None = field(
        default_factory=lambda: Path("var/cache/mis_dashboard.sqlite3")
    )

    # app
    static_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2] / "static")
    host: str = "0.0.0.0"
    port: int = 8481
    jwt_secret: str = "mis-dashboard-demo-secret-change-me"
    jwt_ttl_minutes: int = 60
    auth_cookie_name: str = "mis_access_token"
    auth_cookie_secure: bool = False
    auth_issuer: str = "mis-dashboard"

    table_names: tuple[str, ...] = (
        "MIS_DIM_REGION",
        "MIS_DIM_BRANCH",
        "MIS_DIM_ADVISOR",
        "MIS_DIM_PRODUCT",
        "MIS_DIM_CHANNEL",
        "MIS_DIM_CAMPAIGN",
        "MIS_DIM_DATE",
        "MIS_DIM_ORG_ASSIGNMENT",
        "MIS_FACT_SALES",
        "MIS_FACT_BALANCES",
        "MIS_FACT_CUSTOMER_MOVEMENT",
        "MIS_FACT_CAMPAIGN_RESULTS",
        "MIS_FACT_BRANCH_PLAN",
        "MIS_FACT_ADVISOR_PERF",
        "MIS_DIM_USER",
        "MIS_FACT_PERFORMANCE_SNAPSHOT",
        "MIS_AUDIT_SNAPSHOT_LOAD",
    )

    @classmethod
    def from_env(cls) -> "Settings":
        secure_cookie = os.environ.get("MIS_AUTH_COOKIE_SECURE", "false").lower() \
            in {"1", "true", "yes", "on"}
        return cls(
            nz_host=os.environ.get("NZ_DEV_HOST", "192.168.0.144"),
            nz_port=int(os.environ.get("NZ_DEV_PORT", "5480")),
            nz_database=os.environ.get("NZ_DEV_DATABASE") or os.environ.get("NZ_DEV_DB", "JUST_DATA"),
            nz_user=os.environ.get("NZ_DEV_USER", "admin"),
            nz_password=os.environ.get("NZ_DEV_PASSWORD", "password"),
            nz_min_pool=int(os.environ.get("NZ_MIN_POOL", "1")),
            nz_max_pool=int(os.environ.get("NZ_MAX_POOL", "4")),
            cache_ttl_tables=int(os.environ.get("NZ_CACHE_TTL_TABLES", "86400")),
            cache_ttl_reports=int(os.environ.get("NZ_CACHE_TTL_REPORTS", "120")),
            cache_control_poll_seconds=int(
                os.environ.get("NZ_CACHE_CONTROL_POLL_SECONDS", "300")),
            cache_max_unconfirmed_seconds=int(
                os.environ.get("NZ_CACHE_MAX_UNCONFIRMED_SECONDS", "86400")),
            cache_dataset_name=os.environ.get("NZ_CACHE_DATASET_NAME", "MIS_DASHBOARD"),
            cache_control_table=os.environ.get(
                "NZ_CACHE_CONTROL_TABLE", "MIS_CONTROL_DATASET_LOAD"),
            cache_sqlite_path=(
                Path(sqlite_path)
                if (sqlite_path := os.environ.get(
                    "NZ_CACHE_SQLITE_PATH", "var/cache/mis_dashboard.sqlite3"))
                else None
            ),
            host=os.environ.get("NZ_DASH_HOST", "0.0.0.0"),
            port=int(os.environ.get("NZ_DASH_PORT", "8481")),
            jwt_secret=os.environ.get("MIS_JWT_SECRET", "mis-dashboard-demo-secret-change-me"),
            jwt_ttl_minutes=int(os.environ.get("MIS_JWT_TTL_MINUTES", "60")),
            auth_cookie_name=os.environ.get("MIS_AUTH_COOKIE_NAME", "mis_access_token"),
            auth_cookie_secure=secure_cookie,
            auth_issuer=os.environ.get("MIS_JWT_ISSUER", "mis-dashboard"),
        )
