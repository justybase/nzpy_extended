"""Unit tests for CSV exchange helpers — no database required."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.data_exchange import (  # noqa: E402
    coerce_delimiter,
    csv_to_rows,
    header_columns,
    rows_to_csv,
)


def test_rows_to_csv_roundtrip() -> None:
    cols = [{"ColumnName": "a"}, {"ColumnName": "b"}]
    out = rows_to_csv(cols, [[1, None], ["x", "y"]]).getvalue()
    assert out.splitlines()[0] == "a,b"
    assert csv_to_rows(out)[1] == ["1", None]


def test_coerce_delimiter_defaults() -> None:
    assert coerce_delimiter(None) == ","
    assert coerce_delimiter("") == ","
    assert coerce_delimiter(";") == ";"


def test_header_columns_fallback_names() -> None:
    assert header_columns(["", None]) == [
        ("col1", "VARCHAR(32000)"),
        ("col2", "VARCHAR(32000)"),
    ]
