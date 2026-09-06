"""Schema contracts for the Netezza tables created by seed.py."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from seed import TABLES, TABLE_DDL, build_dataset  # noqa: E402


PRIMARY_KEYS = {
    "MIS_DIM_REGION": ("region_id",),
    "MIS_DIM_BRANCH": ("branch_id",),
    "MIS_DIM_ADVISOR": ("advisor_id",),
    "MIS_DIM_PRODUCT": ("product_id",),
    "MIS_DIM_CHANNEL": ("channel_id",),
    "MIS_DIM_CAMPAIGN": ("campaign_id",),
    "MIS_DIM_DATE": ("calendar_date",),
    "MIS_DIM_ORG_ASSIGNMENT": ("org_assignment_sk",),
    "MIS_FACT_SALES": ("sale_id",),
    "MIS_FACT_BALANCES": ("balance_month", "branch_id", "product_id"),
    "MIS_FACT_CUSTOMER_MOVEMENT": ("movement_month", "branch_id"),
    "MIS_FACT_CAMPAIGN_RESULTS": ("campaign_id", "branch_id"),
    "MIS_FACT_BRANCH_PLAN": ("branch_id", "plan_month"),
    "MIS_FACT_ADVISOR_PERF": ("advisor_id", "perf_month"),
    "MIS_DIM_USER": ("user_id",),
    "MIS_FACT_PERFORMANCE_SNAPSHOT": (
        "snapshot_date", "advisor_id", "historical_branch_id"),
    "MIS_AUDIT_SNAPSHOT_LOAD": ("snapshot_date",),
    "MIS_CONTROL_DATASET_LOAD": ("dataset_name", "version_no"),
}


FOREIGN_KEYS = {
    "MIS_DIM_BRANCH": [("region_id", "MIS_DIM_REGION", ("region_id",))],
    "MIS_DIM_ADVISOR": [("branch_id", "MIS_DIM_BRANCH", ("branch_id",))],
    "MIS_DIM_CAMPAIGN": [
        ("product_id", "MIS_DIM_PRODUCT", ("product_id",)),
        ("start_date", "MIS_DIM_DATE", ("calendar_date",)),
        ("end_date", "MIS_DIM_DATE", ("calendar_date",)),
    ],
    "MIS_DIM_ORG_ASSIGNMENT": [
        ("advisor_id", "MIS_DIM_ADVISOR", ("advisor_id",)),
        ("branch_id", "MIS_DIM_BRANCH", ("branch_id",)),
        ("region_id", "MIS_DIM_REGION", ("region_id",)),
    ],
    "MIS_FACT_SALES": [
        ("branch_id", "MIS_DIM_BRANCH", ("branch_id",)),
        ("advisor_id", "MIS_DIM_ADVISOR", ("advisor_id",)),
        ("product_id", "MIS_DIM_PRODUCT", ("product_id",)),
        ("channel_id", "MIS_DIM_CHANNEL", ("channel_id",)),
        ("sale_date", "MIS_DIM_DATE", ("calendar_date",)),
    ],
    "MIS_FACT_BALANCES": [
        ("branch_id", "MIS_DIM_BRANCH", ("branch_id",)),
        ("product_id", "MIS_DIM_PRODUCT", ("product_id",)),
        ("balance_month", "MIS_DIM_DATE", ("calendar_date",)),
    ],
    "MIS_FACT_CUSTOMER_MOVEMENT": [
        ("branch_id", "MIS_DIM_BRANCH", ("branch_id",)),
        ("movement_month", "MIS_DIM_DATE", ("calendar_date",)),
    ],
    "MIS_FACT_CAMPAIGN_RESULTS": [
        ("campaign_id", "MIS_DIM_CAMPAIGN", ("campaign_id",)),
        ("branch_id", "MIS_DIM_BRANCH", ("branch_id",)),
    ],
    "MIS_FACT_BRANCH_PLAN": [
        ("branch_id", "MIS_DIM_BRANCH", ("branch_id",)),
        ("plan_month", "MIS_DIM_DATE", ("calendar_date",)),
    ],
    "MIS_FACT_ADVISOR_PERF": [
        ("advisor_id", "MIS_DIM_ADVISOR", ("advisor_id",)),
        ("perf_month", "MIS_DIM_DATE", ("calendar_date",)),
    ],
    "MIS_DIM_USER": [
        ("advisor_id", "MIS_DIM_ADVISOR", ("advisor_id",)),
        ("branch_id", "MIS_DIM_BRANCH", ("branch_id",)),
        ("region_id", "MIS_DIM_REGION", ("region_id",)),
    ],
    "MIS_FACT_PERFORMANCE_SNAPSHOT": [
        ("snapshot_date", "MIS_DIM_DATE", ("calendar_date",)),
        ("month_start", "MIS_DIM_DATE", ("calendar_date",)),
        ("advisor_id", "MIS_DIM_ADVISOR", ("advisor_id",)),
        ("historical_branch_id", "MIS_DIM_BRANCH", ("branch_id",)),
        ("historical_region_id", "MIS_DIM_REGION", ("region_id",)),
        ("advisor_id, month_start", "MIS_FACT_ADVISOR_PERF",
         ("advisor_id", "perf_month")),
    ],
    "MIS_AUDIT_SNAPSHOT_LOAD": [
        ("snapshot_date", "MIS_DIM_DATE", ("calendar_date",)),
        ("source_max_date", "MIS_DIM_DATE", ("calendar_date",)),
    ],
}


def compact(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).lower()


@pytest.mark.parametrize("table, columns", PRIMARY_KEYS.items())
def test_seed_declares_primary_key(table: str, columns: tuple[str, ...]) -> None:
    ddl = compact(TABLE_DDL[table])
    expected = f"primary key ({', '.join(columns)})"
    assert expected in ddl


@pytest.mark.parametrize("table, foreign_keys", FOREIGN_KEYS.items())
def test_seed_declares_foreign_keys(table: str, foreign_keys: list[tuple[str, str, tuple[str, ...]]]) -> None:
    ddl = compact(TABLE_DDL[table])
    for columns, parent, parent_columns in foreign_keys:
        expected = (f"foreign key ({columns}) references {parent.lower()}"
                    f" ({', '.join(parent_columns)})")
        assert expected in ddl


def test_table_creation_order_supports_declared_foreign_keys() -> None:
    positions = {name: index for index, name in enumerate(TABLES)}
    for child, foreign_keys in FOREIGN_KEYS.items():
        for _columns, parent, _parent_columns in foreign_keys:
            assert positions[parent] < positions[child]


@pytest.fixture(scope="module")
def dataset() -> dict[str, tuple[list[str], list[list[object]]]]:
    return build_dataset(scale=1)


def test_generated_rows_satisfy_primary_keys_and_foreign_keys(
        dataset: dict[str, tuple[list[str], list[list[object]]]]) -> None:
    key_sets: dict[str, set[tuple[object, ...]]] = {}
    for table, columns in PRIMARY_KEYS.items():
        names, rows = dataset[table]
        indexes = [names.index(column) for column in columns]
        keys = {tuple(row[index] for index in indexes) for row in rows}
        assert len(keys) == len(rows), f"duplicate primary key in {table}"
        key_sets[table] = keys

    for child, foreign_keys in FOREIGN_KEYS.items():
        child_columns, child_rows = dataset[child]
        for child_names, parent, parent_columns in foreign_keys:
            child_columns_tuple = tuple(child_names.split(", "))
            child_indexes = [child_columns.index(column) for column in child_columns_tuple]
            assert all(
                all(value is None for value in (row[index] for index in child_indexes))
                or tuple(row[index] for index in child_indexes)
                in key_sets[parent]
                for row in child_rows
            ), f"invalid foreign key {child}.{child_names} -> {parent}.{parent_columns}"


def test_seed_contains_named_authentication_personas(
        dataset: dict[str, tuple[list[str], list[list[object]]]]) -> None:
    columns, rows = dataset["MIS_DIM_USER"]
    by_code = {row[columns.index("user_code")]: row for row in rows}
    expected = {
        "MIS_SQL_DEV01": "MIS_SQL_DEVELOPER",
        "NET_HEAD01": "NETWORK_HEAD",
        "REG_DIR_RNOR": "REGIONAL_DIRECTOR",
        "BR_DIR_BEL01": "BRANCH_DIRECTOR",
        "ADV_DEMO_P0001": "CUSTOMER_ADVISOR",
        "HQ_FULL01": "HQ_FULL_ACCESS",
        "APP_TESTER01": "APP_TESTER",
    }
    role_index = columns.index("role")
    assert {code: by_code[code][role_index] for code in expected} == expected


def test_seed_contains_published_dataset_version(
        dataset: dict[str, tuple[list[str], list[list[object]]]]) -> None:
    columns, rows = dataset["MIS_CONTROL_DATASET_LOAD"]
    assert len(rows) == 1
    record = dict(zip(columns, rows[0]))
    assert record["dataset_name"] == "MIS_DASHBOARD"
    assert record["version_no"] == 1
    assert record["status"] == "PUBLISHED"
