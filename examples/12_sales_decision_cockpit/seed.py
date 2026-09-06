#!/usr/bin/env python3
"""Create and seed the independent SDC_* demo schema in Netezza.

The generated weekly scorecard is intentionally deterministic.  A few units
receive repeatable late-period pressure so the exception-first UI always has
something useful to explain.
"""

from __future__ import annotations

import datetime as dt
import os
import random
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import nzpy_extended.sync as nzpy

def connection_settings() -> dict[str, Any]:
    """Return an explicitly configured target; the seed never guesses one."""
    required = (
        "NZ_DEV_HOST",
        "NZ_DEV_PORT",
        "NZ_DEV_DATABASE",
        "NZ_DEV_USER",
        "NZ_DEV_PASSWORD",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing required database settings: " + ", ".join(missing))
    try:
        port = int(os.environ["NZ_DEV_PORT"])
    except ValueError as exc:
        raise RuntimeError("NZ_DEV_PORT must be an integer") from exc
    if port <= 0:
        raise RuntimeError("NZ_DEV_PORT must be a positive integer")
    return {
        "user": os.environ["NZ_DEV_USER"],
        "password": os.environ["NZ_DEV_PASSWORD"],
        "host": os.environ["NZ_DEV_HOST"],
        "port": port,
        "database": os.environ["NZ_DEV_DATABASE"],
    }

START_WEEK = dt.date(2025, 9, 1)
LATEST_WEEK = dt.date(2026, 8, 24)
CALENDAR_END = LATEST_WEEK + dt.timedelta(days=6)

TABLES = [
    "SDC_DIM_DATE",
    "SDC_DIM_REGION",
    "SDC_DIM_UNIT",
    "SDC_DIM_SELLER",
    "SDC_DIM_PRODUCT",
    "SDC_DIM_CHANNEL",
    "SDC_FACT_WEEKLY_SCORECARD",
    "SDC_FACT_WEEKLY_TARGET",
    "SDC_AUDIT_DATASET_LOAD",
    "SDC_CONTROL_DATASET_LOAD",
]

TABLE_DDL: dict[str, str] = {
    "SDC_DIM_DATE": """
        CREATE TABLE SDC_DIM_DATE (
            calendar_date DATE NOT NULL,
            week_start DATE NOT NULL,
            week_label NVARCHAR(30) NOT NULL,
            is_business_day SMALLINT NOT NULL,
            CONSTRAINT pk_sdc_dim_date PRIMARY KEY (calendar_date)
        ) DISTRIBUTE ON (calendar_date)""",
    "SDC_DIM_REGION": """
        CREATE TABLE SDC_DIM_REGION (
            region_id INTEGER NOT NULL,
            region_code NVARCHAR(10) NOT NULL,
            region_name NVARCHAR(60) NOT NULL,
            CONSTRAINT pk_sdc_dim_region PRIMARY KEY (region_id),
            CONSTRAINT uq_sdc_dim_region_code UNIQUE (region_code)
        ) DISTRIBUTE ON (region_id)""",
    "SDC_DIM_UNIT": """
        CREATE TABLE SDC_DIM_UNIT (
            unit_id INTEGER NOT NULL,
            unit_code NVARCHAR(12) NOT NULL,
            unit_name NVARCHAR(80) NOT NULL,
            region_id INTEGER NOT NULL,
            city NVARCHAR(40),
            status NVARCHAR(12) NOT NULL,
            CONSTRAINT pk_sdc_dim_unit PRIMARY KEY (unit_id),
            CONSTRAINT uq_sdc_dim_unit_code UNIQUE (unit_code),
            CONSTRAINT fk_sdc_dim_unit_region FOREIGN KEY (region_id)
                REFERENCES SDC_DIM_REGION (region_id)
        ) DISTRIBUTE ON (unit_id)""",
    "SDC_DIM_SELLER": """
        CREATE TABLE SDC_DIM_SELLER (
            seller_id INTEGER NOT NULL,
            seller_code NVARCHAR(12) NOT NULL,
            full_name NVARCHAR(80) NOT NULL,
            unit_id INTEGER NOT NULL,
            role NVARCHAR(40) NOT NULL,
            status NVARCHAR(12) NOT NULL,
            CONSTRAINT pk_sdc_dim_seller PRIMARY KEY (seller_id),
            CONSTRAINT uq_sdc_dim_seller_code UNIQUE (seller_code),
            CONSTRAINT fk_sdc_dim_seller_unit FOREIGN KEY (unit_id)
                REFERENCES SDC_DIM_UNIT (unit_id)
        ) DISTRIBUTE ON (seller_id)""",
    "SDC_DIM_PRODUCT": """
        CREATE TABLE SDC_DIM_PRODUCT (
            product_id INTEGER NOT NULL,
            product_code NVARCHAR(16) NOT NULL,
            product_name NVARCHAR(60) NOT NULL,
            product_group NVARCHAR(40) NOT NULL,
            average_ticket NUMERIC(14, 2) NOT NULL,
            margin_rate NUMERIC(6, 4) NOT NULL,
            CONSTRAINT pk_sdc_dim_product PRIMARY KEY (product_id),
            CONSTRAINT uq_sdc_dim_product_code UNIQUE (product_code)
        ) DISTRIBUTE ON (product_id)""",
    "SDC_DIM_CHANNEL": """
        CREATE TABLE SDC_DIM_CHANNEL (
            channel_id INTEGER NOT NULL,
            channel_name NVARCHAR(40) NOT NULL,
            CONSTRAINT pk_sdc_dim_channel PRIMARY KEY (channel_id),
            CONSTRAINT uq_sdc_dim_channel_name UNIQUE (channel_name)
        ) DISTRIBUTE ON (channel_id)""",
    "SDC_FACT_WEEKLY_SCORECARD": """
        CREATE TABLE SDC_FACT_WEEKLY_SCORECARD (
            week_start DATE NOT NULL,
            unit_id INTEGER NOT NULL,
            seller_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            sales_count INTEGER NOT NULL,
            sales_amount NUMERIC(16, 2) NOT NULL,
            margin_amount NUMERIC(16, 2) NOT NULL,
            leads INTEGER NOT NULL,
            qualified_leads INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            pipeline_value NUMERIC(16, 2) NOT NULL,
            weighted_pipeline_value NUMERIC(16, 2) NOT NULL,
            cancelled_amount NUMERIC(16, 2) NOT NULL,
            CONSTRAINT pk_sdc_fact_weekly_scorecard PRIMARY KEY
                (week_start, seller_id, product_id, channel_id),
            CONSTRAINT fk_sdc_scorecard_date FOREIGN KEY (week_start)
                REFERENCES SDC_DIM_DATE (calendar_date),
            CONSTRAINT fk_sdc_scorecard_unit FOREIGN KEY (unit_id)
                REFERENCES SDC_DIM_UNIT (unit_id),
            CONSTRAINT fk_sdc_scorecard_seller FOREIGN KEY (seller_id)
                REFERENCES SDC_DIM_SELLER (seller_id),
            CONSTRAINT fk_sdc_scorecard_product FOREIGN KEY (product_id)
                REFERENCES SDC_DIM_PRODUCT (product_id),
            CONSTRAINT fk_sdc_scorecard_channel FOREIGN KEY (channel_id)
                REFERENCES SDC_DIM_CHANNEL (channel_id)
        ) DISTRIBUTE ON RANDOM""",
    "SDC_FACT_WEEKLY_TARGET": """
        CREATE TABLE SDC_FACT_WEEKLY_TARGET (
            week_start DATE NOT NULL,
            unit_id INTEGER NOT NULL,
            seller_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            target_count INTEGER NOT NULL,
            target_amount NUMERIC(16, 2) NOT NULL,
            target_margin NUMERIC(16, 2) NOT NULL,
            CONSTRAINT pk_sdc_fact_weekly_target PRIMARY KEY
                (week_start, seller_id, product_id, channel_id),
            CONSTRAINT fk_sdc_target_date FOREIGN KEY (week_start)
                REFERENCES SDC_DIM_DATE (calendar_date),
            CONSTRAINT fk_sdc_target_unit FOREIGN KEY (unit_id)
                REFERENCES SDC_DIM_UNIT (unit_id),
            CONSTRAINT fk_sdc_target_seller FOREIGN KEY (seller_id)
                REFERENCES SDC_DIM_SELLER (seller_id),
            CONSTRAINT fk_sdc_target_product FOREIGN KEY (product_id)
                REFERENCES SDC_DIM_PRODUCT (product_id),
            CONSTRAINT fk_sdc_target_channel FOREIGN KEY (channel_id)
                REFERENCES SDC_DIM_CHANNEL (channel_id)
        ) DISTRIBUTE ON RANDOM""",
    "SDC_AUDIT_DATASET_LOAD": """
        CREATE TABLE SDC_AUDIT_DATASET_LOAD (
            snapshot_week DATE NOT NULL,
            load_id NVARCHAR(40) NOT NULL,
            source_max_date DATE NOT NULL,
            loaded_at NVARCHAR(30) NOT NULL,
            source_actual_amount NUMERIC(16, 2) NOT NULL,
            mart_actual_amount NUMERIC(16, 2) NOT NULL,
            difference_amount NUMERIC(16, 2) NOT NULL,
            status NVARCHAR(12) NOT NULL,
            CONSTRAINT pk_sdc_audit_dataset_load PRIMARY KEY (snapshot_week)
        ) DISTRIBUTE ON (snapshot_week)""",
    "SDC_CONTROL_DATASET_LOAD": """
        CREATE TABLE SDC_CONTROL_DATASET_LOAD (
            dataset_name NVARCHAR(40) NOT NULL,
            version_no INTEGER NOT NULL,
            load_id NVARCHAR(40) NOT NULL,
            source_watermark DATE NOT NULL,
            published_at NVARCHAR(30) NOT NULL,
            status NVARCHAR(16) NOT NULL,
            row_count BIGINT NOT NULL,
            CONSTRAINT pk_sdc_control_dataset_load PRIMARY KEY
                (dataset_name, version_no),
            CONSTRAINT uq_sdc_control_dataset_load_id UNIQUE (load_id)
        ) DISTRIBUTE ON RANDOM""",
}

LOAD_COLUMNS = {
    "SDC_DIM_DATE": [("calendar_date", "DATE"), ("week_start", "DATE"), ("week_label", "NVARCHAR(30)"), ("is_business_day", "SMALLINT")],
    "SDC_DIM_REGION": [("region_id", "INTEGER"), ("region_code", "NVARCHAR(10)"), ("region_name", "NVARCHAR(60)")],
    "SDC_DIM_UNIT": [("unit_id", "INTEGER"), ("unit_code", "NVARCHAR(12)"), ("unit_name", "NVARCHAR(80)"), ("region_id", "INTEGER"), ("city", "NVARCHAR(40)"), ("status", "NVARCHAR(12)")],
    "SDC_DIM_SELLER": [("seller_id", "INTEGER"), ("seller_code", "NVARCHAR(12)"), ("full_name", "NVARCHAR(80)"), ("unit_id", "INTEGER"), ("role", "NVARCHAR(40)"), ("status", "NVARCHAR(12)")],
    "SDC_DIM_PRODUCT": [("product_id", "INTEGER"), ("product_code", "NVARCHAR(16)"), ("product_name", "NVARCHAR(60)"), ("product_group", "NVARCHAR(40)"), ("average_ticket", "NUMERIC(14, 2)"), ("margin_rate", "NUMERIC(6, 4)")],
    "SDC_DIM_CHANNEL": [("channel_id", "INTEGER"), ("channel_name", "NVARCHAR(40)")],
    "SDC_FACT_WEEKLY_SCORECARD": [
        ("week_start", "DATE"), ("unit_id", "INTEGER"), ("seller_id", "INTEGER"),
        ("product_id", "INTEGER"), ("channel_id", "INTEGER"), ("sales_count", "INTEGER"),
        ("sales_amount", "NUMERIC(16, 2)"), ("margin_amount", "NUMERIC(16, 2)"),
        ("leads", "INTEGER"), ("qualified_leads", "INTEGER"), ("wins", "INTEGER"),
        ("pipeline_value", "NUMERIC(16, 2)"), ("weighted_pipeline_value", "NUMERIC(16, 2)"),
        ("cancelled_amount", "NUMERIC(16, 2)"),
    ],
    "SDC_FACT_WEEKLY_TARGET": [
        ("week_start", "DATE"), ("unit_id", "INTEGER"), ("seller_id", "INTEGER"),
        ("product_id", "INTEGER"), ("channel_id", "INTEGER"), ("target_count", "INTEGER"),
        ("target_amount", "NUMERIC(16, 2)"), ("target_margin", "NUMERIC(16, 2)"),
    ],
    "SDC_AUDIT_DATASET_LOAD": [
        ("snapshot_week", "DATE"), ("load_id", "NVARCHAR(40)"), ("source_max_date", "DATE"),
        ("loaded_at", "NVARCHAR(30)"), ("source_actual_amount", "NUMERIC(16, 2)"),
        ("mart_actual_amount", "NUMERIC(16, 2)"), ("difference_amount", "NUMERIC(16, 2)"),
        ("status", "NVARCHAR(12)"),
    ],
    "SDC_CONTROL_DATASET_LOAD": [
        ("dataset_name", "NVARCHAR(40)"), ("version_no", "INTEGER"), ("load_id", "NVARCHAR(40)"),
        ("source_watermark", "DATE"), ("published_at", "NVARCHAR(30)"),
        ("status", "NVARCHAR(16)"), ("row_count", "BIGINT"),
    ],
}

REGIONS = [
    (1, "NORTH", "North Region"),
    (2, "WEST", "West Region"),
    (3, "CENTRAL", "Central Region"),
    (4, "SOUTH", "South Region"),
]
UNIT_NAMES = [
    ("N01", "North City", "Newcastle"), ("N02", "North Coast", "Sunderland"),
    ("N03", "North Market", "York"), ("W01", "West City", "Liverpool"),
    ("W02", "West Coast", "Chester"), ("W03", "West Market", "Preston"),
    ("C01", "Central City", "Birmingham"), ("C02", "Central Market", "Coventry"),
    ("C03", "Central South", "Oxford"), ("S01", "South City", "Bristol"),
    ("S02", "South Coast", "Brighton"), ("S03", "South Market", "Southampton"),
]
PRODUCTS = [
    (1, "LEND", "Lending", "Lending", 18_000.0, 0.085),
    (2, "PROT", "Protection", "Protection", 2_400.0, 0.42),
    (3, "INV", "Investments", "Investments", 12_500.0, 0.095),
    (4, "PAY", "Payments", "Payments", 850.0, 0.22),
    (5, "DEP", "Deposits", "Deposits", 9_000.0, 0.035),
]
CHANNELS = [(1, "Branch"), (2, "Mobile"), (3, "Web"), (4, "Contact centre"), (5, "Partner")]
SELLER_NAMES = [
    "Alex Morgan", "Jamie Ellis", "Taylor Reed", "Jordan Blake",
    "Morgan Hayes", "Casey Taylor", "Riley Cooper", "Avery Brooks",
]


def _weeks() -> list[dt.date]:
    result: list[dt.date] = []
    current = START_WEEK
    while current <= LATEST_WEEK:
        result.append(current)
        current += dt.timedelta(days=7)
    return result


def _season(week_index: int) -> float:
    return [0.92, 0.96, 1.03, 1.08, 1.02, 0.98, 1.06, 1.12, 1.0, 0.94, 1.04, 1.1][week_index % 12]


def _performance_factor(unit_id: int, week: dt.date) -> float:
    if week >= dt.date(2026, 7, 13) and unit_id in {3, 7, 10}:
        return 0.66
    if week >= dt.date(2026, 7, 13) and unit_id in {5, 11}:
        return 0.84
    if week >= dt.date(2026, 7, 13) and unit_id in {2, 8}:
        return 1.16
    return 1.0


def _pipeline_factor(unit_id: int, week: dt.date) -> float:
    if week >= dt.date(2026, 7, 13) and unit_id in {3, 7, 10}:
        return 0.52
    if week >= dt.date(2026, 7, 13) and unit_id in {2, 8}:
        return 1.35
    return 1.04


def gen_dates() -> list[list[Any]]:
    rows: list[list[Any]] = []
    current = START_WEEK
    while current <= CALENDAR_END:
        week = current - dt.timedelta(days=current.weekday())
        rows.append([
            current.isoformat(), week.isoformat(), f"Week of {week.isoformat()}",
            1 if current.weekday() < 5 else 0,
        ])
        current += dt.timedelta(days=1)
    return rows


def gen_regions() -> list[list[Any]]:
    return [list(row) for row in REGIONS]


def gen_units(count: int) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for index, (code, name, city) in enumerate(UNIT_NAMES[:count], start=1):
        region_id = (index - 1) // 3 + 1
        rows.append([index, code, name, region_id, city, "ACTIVE"])
    return rows


def gen_sellers(unit_count: int, sellers_per_unit: int) -> list[list[Any]]:
    rows: list[list[Any]] = []
    seller_id = 1
    for unit_id in range(1, unit_count + 1):
        for local_index in range(sellers_per_unit):
            name = SELLER_NAMES[(seller_id - 1) % len(SELLER_NAMES)]
            rows.append([seller_id, f"SEL{seller_id:03d}", name, unit_id, "Sales specialist", "ACTIVE"])
            seller_id += 1
    return rows


def gen_products() -> list[list[Any]]:
    return [list(row) for row in PRODUCTS]


def gen_channels() -> list[list[Any]]:
    return [list(row) for row in CHANNELS]


def gen_scorecard_and_targets(
    rng: random.Random,
    sellers: list[list[Any]],
) -> tuple[list[list[Any]], list[list[Any]]]:
    scorecard: list[list[Any]] = []
    targets: list[list[Any]] = []
    base_counts = {1: 7, 2: 5, 3: 4, 4: 8, 5: 5}
    channel_factors = {1: 1.0, 2: 0.82, 3: 0.72, 4: 0.9, 5: 0.62}
    for week_index, week in enumerate(_weeks()):
        season = _season(week_index)
        for seller in sellers:
            seller_id, _code, _name, unit_id = seller[:4]
            seller_factor = 0.9 + ((seller_id - 1) % 4) * 0.06
            performance = _performance_factor(unit_id, week)
            pipeline_factor = _pipeline_factor(unit_id, week)
            for product_id, _product_code, _name, _group, ticket, margin_rate in PRODUCTS:
                for channel_id, _channel_name in CHANNELS:
                    base = base_counts[product_id] * seller_factor * channel_factors[channel_id] * season
                    target_count = max(1, int(round(base)))
                    target_amount = round(target_count * ticket, 2)
                    target_margin = round(target_amount * margin_rate, 2)
                    noise = rng.uniform(0.88, 1.12)
                    sales_count = max(0, int(round(target_count * performance * noise)))
                    amount_noise = rng.uniform(0.94, 1.06)
                    sales_amount = round(sales_count * ticket * amount_noise, 2)
                    margin_amount = round(sales_amount * margin_rate, 2)
                    leads = max(1, int(round(target_count * 10 * rng.uniform(0.9, 1.1))))
                    conversion_factor = 0.34 if performance < 0.9 else 0.43
                    qualified = min(leads, max(0, int(round(leads * conversion_factor * rng.uniform(0.9, 1.1)))))
                    wins = sales_count
                    pipeline = round(target_amount * pipeline_factor * rng.uniform(0.85, 1.15), 2)
                    weighted = round(pipeline * rng.uniform(0.45, 0.65), 2)
                    cancelled = round(sales_amount * rng.uniform(0.018, 0.052) * (1.5 if performance < 0.9 else 1.0), 2)
                    scorecard.append([
                        week.isoformat(), unit_id, seller_id, product_id, channel_id,
                        sales_count, sales_amount, margin_amount, leads, qualified,
                        wins, pipeline, weighted, cancelled,
                    ])
                    targets.append([
                        week.isoformat(), unit_id, seller_id, product_id, channel_id,
                        target_count, target_amount, target_margin,
                    ])
    return scorecard, targets


def gen_audit(scorecard: list[list[Any]], targets: list[list[Any]]) -> list[list[Any]]:
    actual_by_week: dict[str, float] = {}
    for row in scorecard:
        actual_by_week[row[0]] = actual_by_week.get(row[0], 0.0) + float(row[6])
    target_by_week: dict[str, float] = {}
    for row in targets:
        target_by_week[row[0]] = target_by_week.get(row[0], 0.0) + float(row[6])
    rows = []
    for week in _weeks():
        key = week.isoformat()
        actual = round(actual_by_week.get(key, 0.0), 2)
        target = round(target_by_week.get(key, 0.0), 2)
        rows.append([
            key, f"SDC-{week.strftime('%Y%m%d')}", (week + dt.timedelta(days=6)).isoformat(),
            f"{week.isoformat()}T06:00:00", actual, actual, 0.0, "PASS",
        ])
    return rows


def build_dataset(scale: float = 1.0) -> dict[str, tuple[list[str], list[list[Any]]]]:
    """Build the full database-free dataset used by tests and demos."""
    if scale <= 0:
        raise ValueError("scale must be greater than zero")
    unit_count = max(2, min(len(UNIT_NAMES), round(12 * scale)))
    sellers_per_unit = max(2, min(8, round(8 * scale)))
    regions = gen_regions()
    units = gen_units(unit_count)
    sellers = gen_sellers(unit_count, sellers_per_unit)
    scorecard, targets = gen_scorecard_and_targets(random.Random(20260906), sellers)
    audit = gen_audit(scorecard, targets)
    total_rows = sum(len(rows) for rows in (regions, units, sellers, scorecard, targets, audit))
    control = [[
        "SALES_DECISION_COCKPIT", 1, f"SDC-{LATEST_WEEK.strftime('%Y%m%d')}",
        (LATEST_WEEK + dt.timedelta(days=6)).isoformat(),
        f"{LATEST_WEEK.isoformat()}T06:00:00", "PUBLISHED", total_rows,
    ]]
    table_rows = {
        "SDC_DIM_DATE": gen_dates(),
        "SDC_DIM_REGION": regions,
        "SDC_DIM_UNIT": units,
        "SDC_DIM_SELLER": sellers,
        "SDC_DIM_PRODUCT": gen_products(),
        "SDC_DIM_CHANNEL": gen_channels(),
        "SDC_FACT_WEEKLY_SCORECARD": scorecard,
        "SDC_FACT_WEEKLY_TARGET": targets,
        "SDC_AUDIT_DATASET_LOAD": audit,
        "SDC_CONTROL_DATASET_LOAD": control,
    }
    return {
        name: ([column for column, _type in LOAD_COLUMNS[name]], table_rows[name])
        for name in TABLES
    }


def table_exists(cur: Any, name: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM _v_table WHERE tablename = ? AND objtype = 'TABLE'",
        (name,),
    )
    return cur.fetchone()[0] > 0


def create_schema(conn: Any) -> None:
    cur = conn.cursor()
    for name in reversed(TABLES):
        if table_exists(cur, name):
            cur.execute(f"DROP TABLE {name}")
            print(f"  dropped {name}")
    for name in TABLES:
        cur.execute(TABLE_DDL[name].strip())
        print(f"  created {name}")
    conn.commit()


def load_table(conn: Any, name: str, rows: Iterator[list[Any]]) -> int:
    return conn.load_data(
        table_name=name,
        rows=rows,
        columns=LOAD_COLUMNS[name],
        encoding="UTF8",
        create_if_missing=False,
    )


def verify(conn: Any) -> None:
    cur = conn.cursor()
    print("\nVerification:")
    for name in TABLES:
        cur.execute(f"SELECT COUNT(*) FROM {name}")
        print(f"  {name:<34} {cur.fetchone()[0]:>9,} rows")
    cur.execute("SELECT SUM(sales_amount) FROM SDC_FACT_WEEKLY_SCORECARD")
    actual = cur.fetchone()[0]
    cur.execute("SELECT SUM(target_amount) FROM SDC_FACT_WEEKLY_TARGET")
    target = cur.fetchone()[0]
    print("  total sales / target:", actual, "/", target)


def main() -> int:
    scale = 1.0
    if "--scale" in sys.argv:
        try:
            scale = float(sys.argv[sys.argv.index("--scale") + 1])
        except (IndexError, ValueError):
            print("Usage: python seed.py [--scale VALUE]")
            return 2
    if scale <= 0:
        print("--scale must be > 0")
        return 2
    connection = connection_settings()
    print(f"Connecting to {connection['host']}:{connection['port']}/{connection['database']} as {connection['user']} ...")
    started = time.time()
    conn = nzpy.connect(**connection)
    try:
        print("Recreating SDC schema ...")
        create_schema(conn)
        dataset = build_dataset(scale)
        print(f"Generated {sum(len(rows) for _columns, rows in dataset.values()):,} rows")
        for name, (_columns, rows) in dataset.items():
            loaded = load_table(conn, name, iter(rows))
            print(f"  {name}: {loaded:,} rows")
        verify(conn)
    finally:
        conn.close()
    print(f"\nDone in {time.time() - started:.1f}s. Start with: python main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
