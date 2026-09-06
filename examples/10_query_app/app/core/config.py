"""Application settings, read from the environment.

Connection uses the standard NZ_DEV_* variables (same convention as the
driver tests and the other examples):

    NZ_DEV_HOST / NZ_DEV_PORT / NZ_DEV_DB (or NZ_DEV_DATABASE)
    NZ_DEV_USER / NZ_DEV_PASSWORD / NZ_MIN_POOL / NZ_MAX_POOL
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    nz_host: str = "192.168.0.144"
    nz_port: int = 5480
    nz_database: str = "JUST_DATA"
    nz_user: str = "admin"
    nz_password: str = "password"
    nz_min_pool: int = 2
    nz_max_pool: int = 8

    default_query_timeout: float = 30.0
    max_import_rows: int = 50_000
    result_limit: int = 1_000_000
    result_chunk_size: int = 1_000
    result_page_size: int = 200
    result_session_ttl: int = 3_600
    result_storage_dir: Path = field(
        default_factory=lambda: Path("/tmp/nzpy_extended-query-sessions")
    )
    schema_cache_ttl: int = 600  # seconds for schema/table list cache
    preview_ttl: int = 300

    static_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[2] / "static"
    )
    host: str = "0.0.0.0"
    port: int = 8480

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            nz_host=os.environ.get("NZ_DEV_HOST", "192.168.0.144"),
            nz_port=int(os.environ.get("NZ_DEV_PORT", "5480")),
            nz_database=os.environ.get("NZ_DEV_DATABASE")
            or os.environ.get("NZ_DEV_DB", "JUST_DATA"),
            nz_user=os.environ.get("NZ_DEV_USER", "admin"),
            nz_password=os.environ.get("NZ_DEV_PASSWORD", "password"),
            nz_min_pool=int(os.environ.get("NZ_MIN_POOL", "2")),
            nz_max_pool=int(os.environ.get("NZ_MAX_POOL", "8")),
            default_query_timeout=float(os.environ.get("NZ_QUERY_TIMEOUT", "30")),
            result_limit=int(os.environ.get("NZ_RESULT_ROW_LIMIT", "1000000")),
            result_chunk_size=int(os.environ.get("NZ_RESULT_CHUNK_SIZE", "1000")),
            result_page_size=int(os.environ.get("NZ_RESULT_PAGE_SIZE", "200")),
            result_session_ttl=int(os.environ.get("NZ_RESULT_SESSION_TTL", "3600")),
            result_storage_dir=Path(
                os.environ.get("NZ_RESULT_STORAGE_DIR", "/tmp/nzpy_extended-query-sessions")
            ),
            host=os.environ.get("NZ_APP_HOST", "0.0.0.0"),
            port=int(os.environ.get("NZ_APP_PORT", "8480")),
        )
