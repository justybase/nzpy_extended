"""Tests for documentation indexes and generated machine-readable contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_ROOT))

from tools.verify_documentation import validate  # noqa: E402


def test_documentation_contracts_are_consistent() -> None:
    assert validate() == []


def test_openapi_declares_cookie_security_and_protected_paths() -> None:
    specification = json.loads(
        (EXAMPLE_ROOT / "docs/contracts/openapi.json").read_text(encoding="utf-8")
    )
    scheme = specification["components"]["securitySchemes"]["accessCookie"]
    assert scheme["type"] == "apiKey"
    assert scheme["in"] == "cookie"
    assert scheme["name"] == "mis_access_token"
    assert specification["paths"]["/api/report/{report_id}"]["get"]["security"] == [
        {"accessCookie": []}
    ]
    assert "/api/auth/login" not in specification["paths"] or "security" not in specification[
        "paths"]["/api/auth/login"]["post"]


@pytest.mark.parametrize(
    "document",
    [
        "docs/index.md",
        "docs/implementation-guide.md",
        "docs/production-adaptation.md",
        "docs/operations.md",
    ],
)
def test_key_documents_declare_production_boundary(document: str) -> None:
    text = (EXAMPLE_ROOT / document).read_text(encoding="utf-8")
    assert "Production adaptation" in text
