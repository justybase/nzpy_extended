"""Unit tests for SQL completion context helpers — no database required.

The JS twin lives in static/js/completion.js; keep the regexes aligned.
Run from the example directory:  python -m pytest tests/ -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.completion_context import (  # noqa: E402
    detect_context,
    dot_prefix,
    extract_from_tables,
    qualified_prefix,
    split_object_name,
)


def test_table_context_after_from() -> None:
    assert detect_context("SELECT * FROM ") == "table"
    assert detect_context("select a from mysch.") == "column-dot"
    assert detect_context("SELECT * FROM mytab JOIN ") == "table"


def test_column_context() -> None:
    assert detect_context("SELECT ") == "column"
    assert detect_context("SELECT a,  WHERE x=1 AND ") == "column"


def test_dot_prefix() -> None:
    assert dot_prefix("SELECT t.") == "t"
    assert dot_prefix("SELECT sch.tab.") == "tab"
    assert dot_prefix("SELECT 1") == ""


def test_qualified_prefix_preserves_netezza_qualification() -> None:
    assert qualified_prefix("FROM BAZ") == ([], "BAZ")
    assert qualified_prefix("FROM BAZA.") == (["BAZA"], "")
    assert qualified_prefix("FROM BAZA..") == (["BAZA", ""], "")
    assert qualified_prefix("FROM BAZA.SCHEMA.TAB") == (["BAZA", "SCHEMA"], "TAB")


def test_split_object_name_handles_database_without_schema() -> None:
    assert split_object_name("JUST_DATA..DIMDATE", "JUST_DATA", "PUBLIC") == ("DIMDATE", None, "JUST_DATA")


def test_extract_from_tables_with_aliases() -> None:
    tables = extract_from_tables(
        "SELECT a.id FROM admin.users a INNER JOIN admin.orders AS o ON a.id=o.uid"
    )
    assert tables[0] == {"name": "ADMIN.USERS", "alias": "A"}
    assert tables[1] == {"name": "ADMIN.ORDERS", "alias": "O"}


def test_extract_ignores_keyword_alias() -> None:
    tables = extract_from_tables("SELECT * FROM mytab WHERE x=1")
    assert tables == [{"name": "MYTAB", "alias": "MYTAB"}]
