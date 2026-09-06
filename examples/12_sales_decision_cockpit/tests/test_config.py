"""Configuration must never choose a database target implicitly."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_ROOT))

from app.core.config import Settings  # noqa: E402
from seed import connection_settings  # noqa: E402


REQUIRED = (
    "NZ_DEV_HOST",
    "NZ_DEV_PORT",
    "NZ_DEV_DATABASE",
    "NZ_DEV_USER",
    "NZ_DEV_PASSWORD",
)


def test_application_settings_require_explicit_connection_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="Missing required database settings"):
        Settings.from_env()


def test_seed_connection_settings_require_explicit_connection_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="Missing required database settings"):
        connection_settings()
