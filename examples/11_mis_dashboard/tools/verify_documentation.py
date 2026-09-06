#!/usr/bin/env python3
"""Verify the local documentation and machine-readable contracts."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLE_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_ROOT))

REQUIRED_FILES = (
    "README.md",
    "docs/index.md",
    "docs/implementation-guide.md",
    "docs/cache.md",
    "docs/authentication.md",
    "docs/data_dictionary.md",
    "docs/erd.md",
    "docs/production-adaptation.md",
    "docs/operations.md",
    "docs/contracts/README.md",
    "docs/contracts/api-conventions.md",
    "docs/contracts/data-contract.json",
    "docs/contracts/openapi.json",
    "docs/adr/README.md",
    "docs/adr/template.md",
)

ADR_FILES = (
    "docs/adr/0001-documentation-and-contract-sources.md",
    "docs/adr/0002-etl-publication-and-cache-generation.md",
    "docs/adr/0003-local-sqlite-snapshot.md",
    "docs/adr/0004-point-in-time-quality-gates.md",
    "docs/adr/0005-backend-scope-authorization.md",
    "docs/adr/0006-demo-identity-boundary.md",
    "docs/adr/0007-api-contract-and-versioning.md",
)

MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
REQUIRED_TABLE_FIELDS = {
    "name",
    "category",
    "grain",
    "primary_key",
    "date_columns",
    "cache_policy",
    "production_status",
    "owner_role",
}


def _load_settings() -> Any:
    from app.core.config import Settings

    return Settings()


def _relative_links() -> list[str]:
    errors: list[str] = []
    documents = [EXAMPLE_ROOT / "README.md"]
    documents.extend((EXAMPLE_ROOT / "docs").rglob("*.md"))
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for target in MARKDOWN_LINK.findall(text):
            target = target.strip().split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            candidate = (document.parent / target).resolve()
            try:
                candidate.relative_to(EXAMPLE_ROOT.resolve())
            except ValueError:
                errors.append(f"{document.relative_to(EXAMPLE_ROOT)}: link escapes example: {target}")
                continue
            if not candidate.exists():
                errors.append(f"{document.relative_to(EXAMPLE_ROOT)}: missing link: {target}")
    return errors


def _validate_data_contract() -> list[str]:
    path = EXAMPLE_ROOT / "docs/contracts/data-contract.json"
    errors: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"data contract cannot be loaded: {exc}"]

    if payload.get("contract_version") != 1:
        errors.append("data contract must have contract_version = 1")
    if payload.get("dataset_name") != "MIS_DASHBOARD":
        errors.append("data contract dataset_name must be MIS_DASHBOARD")
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        return errors + ["data contract tables must be a non-empty list"]

    names: list[str] = []
    for index, table in enumerate(tables):
        if not isinstance(table, dict):
            errors.append(f"data contract table {index} is not an object")
            continue
        missing = sorted(REQUIRED_TABLE_FIELDS - set(table))
        if missing:
            errors.append(f"data contract table {index} missing: {', '.join(missing)}")
        name = table.get("name")
        if isinstance(name, str):
            names.append(name)
        else:
            errors.append(f"data contract table {index} has no string name")

    if len(names) != len(set(names)):
        errors.append("data contract contains duplicate table names")

    settings = _load_settings()
    configured = set(settings.table_names)
    expected = configured | {settings.cache_control_table}
    if set(names) != expected:
        errors.append(
            "data contract table names differ from Settings.table_names plus "
            f"{settings.cache_control_table}: "
            f"missing={sorted(expected - set(names))}, "
            f"extra={sorted(set(names) - expected)}"
        )

    by_name = {table.get("name"): table for table in tables if isinstance(table, dict)}
    if by_name.get(settings.cache_control_table, {}).get("category") != "control":
        errors.append("configured control table must have category=control")
    if by_name.get("MIS_FACT_PERFORMANCE_SNAPSHOT", {}).get("category") != "lazy":
        errors.append("performance snapshot must have category=lazy")
    if settings.cache_control_table in configured:
        errors.append("control table must not be in Settings.table_names")

    categories = {table.get("category") for table in tables if isinstance(table, dict)}
    if not {"eager", "lazy", "audit", "control"}.issubset(categories):
        errors.append("data contract must contain eager, lazy, audit and control categories")
    return errors


def _validate_openapi_snapshot() -> list[str]:
    from tools.export_openapi import build_openapi, serialise

    path = EXAMPLE_ROOT / "docs/contracts/openapi.json"
    errors: list[str] = []
    try:
        actual = path.read_text(encoding="utf-8")
        json.loads(actual)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"OpenAPI contract cannot be loaded: {exc}"]
    expected = serialise(build_openapi())
    if actual != expected:
        errors.append("docs/contracts/openapi.json differs from FastAPI output")
    return errors


def validate() -> list[str]:
    errors: list[str] = []
    for relative in REQUIRED_FILES + ADR_FILES:
        if not (EXAMPLE_ROOT / relative).is_file():
            errors.append(f"missing required documentation file: {relative}")
    errors.extend(_relative_links())
    errors.extend(_validate_data_contract())
    errors.extend(_validate_openapi_snapshot())
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Documentation and contracts are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
