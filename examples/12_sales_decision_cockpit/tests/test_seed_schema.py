"""Schema and referential-integrity checks for the SDC seed."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_ROOT))

from seed import TABLES, TABLE_DDL, build_dataset  # noqa: E402


PRIMARY_KEYS = {
    "SDC_DIM_DATE": ("calendar_date",),
    "SDC_DIM_REGION": ("region_id",),
    "SDC_DIM_UNIT": ("unit_id",),
    "SDC_DIM_SELLER": ("seller_id",),
    "SDC_DIM_PRODUCT": ("product_id",),
    "SDC_DIM_CHANNEL": ("channel_id",),
    "SDC_FACT_WEEKLY_SCORECARD": ("week_start", "seller_id", "product_id", "channel_id"),
    "SDC_FACT_WEEKLY_TARGET": ("week_start", "seller_id", "product_id", "channel_id"),
    "SDC_AUDIT_DATASET_LOAD": ("snapshot_week",),
    "SDC_CONTROL_DATASET_LOAD": ("dataset_name", "version_no"),
}

FOREIGN_KEYS = {
    "SDC_DIM_UNIT": [("region_id", "SDC_DIM_REGION", ("region_id",))],
    "SDC_DIM_SELLER": [("unit_id", "SDC_DIM_UNIT", ("unit_id",))],
    "SDC_FACT_WEEKLY_SCORECARD": [
        ("week_start", "SDC_DIM_DATE", ("calendar_date",)),
        ("unit_id", "SDC_DIM_UNIT", ("unit_id",)),
        ("seller_id", "SDC_DIM_SELLER", ("seller_id",)),
        ("product_id", "SDC_DIM_PRODUCT", ("product_id",)),
        ("channel_id", "SDC_DIM_CHANNEL", ("channel_id",)),
    ],
    "SDC_FACT_WEEKLY_TARGET": [
        ("week_start", "SDC_DIM_DATE", ("calendar_date",)),
        ("unit_id", "SDC_DIM_UNIT", ("unit_id",)),
        ("seller_id", "SDC_DIM_SELLER", ("seller_id",)),
        ("product_id", "SDC_DIM_PRODUCT", ("product_id",)),
        ("channel_id", "SDC_DIM_CHANNEL", ("channel_id",)),
    ],
}


def compact(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).lower()


@pytest.mark.parametrize("table, columns", PRIMARY_KEYS.items())
def test_seed_declares_primary_key(table: str, columns: tuple[str, ...]) -> None:
    ddl = compact(TABLE_DDL[table])
    assert f"primary key ({', '.join(columns)})" in ddl


@pytest.mark.parametrize("table, foreign_keys", FOREIGN_KEYS.items())
def test_seed_declares_foreign_keys(table: str, foreign_keys) -> None:
    ddl = compact(TABLE_DDL[table])
    for columns, parent, parent_columns in foreign_keys:
        expected = f"foreign key ({columns}) references {parent.lower()} ({', '.join(parent_columns)})"
        assert expected in ddl


def test_table_order_supports_foreign_keys() -> None:
    positions = {name: index for index, name in enumerate(TABLES)}
    for child, foreign_keys in FOREIGN_KEYS.items():
        for _columns, parent, _parent_columns in foreign_keys:
            assert positions[parent] < positions[child]


def test_dataset_is_deterministic_and_referentially_valid() -> None:
    first = build_dataset(scale=0.25)
    second = build_dataset(scale=0.25)
    assert first == second
    keys: dict[str, set[tuple[Any, ...]]] = {}
    for table, columns in PRIMARY_KEYS.items():
        names, rows = first[table]
        indexes = [names.index(column) for column in columns]
        key_set = {tuple(row[index] for index in indexes) for row in rows}
        assert len(key_set) == len(rows), f"duplicate primary key in {table}"
        keys[table] = key_set
    for child, foreign_keys in FOREIGN_KEYS.items():
        child_names, child_rows = first[child]
        for child_column, parent, parent_columns in foreign_keys:
            indexes = [child_names.index(column) for column in child_column.split(", ")]
            assert all(tuple(row[index] for index in indexes) in keys[parent] for row in child_rows)


def test_latest_control_row_is_published() -> None:
    columns, rows = build_dataset(scale=0.25)["SDC_CONTROL_DATASET_LOAD"]
    record = dict(zip(columns, rows[0]))
    assert record["dataset_name"] == "SALES_DECISION_COCKPIT"
    assert record["status"] == "PUBLISHED"
