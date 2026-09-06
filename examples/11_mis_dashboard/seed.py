#!/usr/bin/env python3
"""
Seed the JUST_DATA database (Netezza) with a demo MIS star schema used by the
retail sales-network dashboard (examples/11_mis_dashboard).

All object and column names are in English. The sample data is generated
deterministically (fixed RNG seeds), so every run produces the same numbers.

Tables created / recreated:
    MIS_DIM_REGION, MIS_DIM_BRANCH, MIS_DIM_ADVISOR, MIS_DIM_PRODUCT,
    MIS_DIM_CHANNEL, MIS_DIM_CAMPAIGN
    MIS_FACT_SALES              - product sales list (booked / cancelled)
    MIS_FACT_BALANCES           - month-end balance snapshots (ROR, savings, deposits, investments)
    MIS_FACT_CUSTOMER_MOVEMENT  - monthly client acquisition / churn / product penetration
    MIS_FACT_CAMPAIGN_RESULTS   - campaign effectiveness per branch
    MIS_FACT_BRANCH_PLAN        - monthly sales plans per branch (sum of advisor plans)
    MIS_FACT_ADVISOR_PERF       - monthly advisor plans, KPI scores and ratings
    MIS_DIM_USER                - role personas and demo users for the login flow
    MIS_DIM_DATE                - reporting calendar (calendar and business-day attributes)
    MIS_DIM_ORG_ASSIGNMENT      - advisor -> branch -> region history (SCD type 2)
    MIS_FACT_PERFORMANCE_SNAPSHOT - daily and accumulating MTD reporting mart
    MIS_AUDIT_SNAPSHOT_LOAD     - freshness and reconciliation evidence
    MIS_CONTROL_DATASET_LOAD    - latest published ETL dataset versions

Usage:
    python seed.py             # full dataset (~180k sales rows)
    python seed.py --rows 10   # scaled-down dataset for quick tests

Connection is configured via NZ_DEV_HOST / NZ_DEV_PORT / NZ_DEV_DATABASE /
NZ_DEV_USER / NZ_DEV_PASSWORD environment variables.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import random
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import nzpy_extended.sync as nzpy

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NZ = dict(
    user=os.environ.get("NZ_DEV_USER", "admin"),
    password=os.environ.get("NZ_DEV_PASSWORD", "password"),
    host=os.environ.get("NZ_DEV_HOST", "192.168.0.144"),
    port=int(os.environ.get("NZ_DEV_PORT", "5480")),
    database=os.environ.get("NZ_DEV_DATABASE", "JUST_DATA"),
)

START = dt.date(2024, 1, 1)
# The final month is intentionally incomplete.  2026-08-15 is the canonical
# example used throughout the dashboard: an August snapshot may only contain
# activity from 1 through 15 August.
END = dt.date(2026, 8, 15)
FIRST_MONTH = dt.date(2024, 1, 1)

# Keep a small, deterministic group of visible Champions League qualifiers in
# every demo scale. The regular generator is intentionally stochastic and its
# reduced fixtures (for example ``--rows 10``) can otherwise leave every
# advisor below the minimum-volume guardrail.
LEAGUE_SHOWCASE_ADVISOR_IDS = (64, 82, 92, 101, 135, 187)
LEAGUE_MIN_BOOKED_SALES = 10
LEAGUE_SHOWCASE_SALE_AMOUNT = 12_000.0

SEASON = [0.85, 0.90, 1.05, 1.00, 1.10, 1.00, 0.90, 0.80, 1.10, 1.05, 1.00, 1.15]

TABLES = [
    "MIS_DIM_REGION",
    "MIS_DIM_BRANCH",
    "MIS_DIM_ADVISOR",
    "MIS_DIM_PRODUCT",
    "MIS_DIM_CHANNEL",
    "MIS_DIM_DATE",
    "MIS_DIM_CAMPAIGN",
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
    "MIS_CONTROL_DATASET_LOAD",
]

TABLE_DDL = {
    "MIS_DIM_REGION": """
        CREATE TABLE MIS_DIM_REGION (
            region_id   INTEGER NOT NULL,
            region_code NVARCHAR(10)  NOT NULL,
            region_name NVARCHAR(60) NOT NULL,
            area_name   NVARCHAR(60),
            seat_city   NVARCHAR(40),
            CONSTRAINT pk_mis_dim_region PRIMARY KEY (region_id),
            CONSTRAINT uq_mis_dim_region_code UNIQUE (region_code)
        ) DISTRIBUTE ON (region_id)""",
    "MIS_DIM_BRANCH": """
        CREATE TABLE MIS_DIM_BRANCH (
            branch_id   INTEGER NOT NULL,
            branch_code NVARCHAR(10)  NOT NULL,
            branch_name NVARCHAR(80) NOT NULL,
            region_id   INTEGER NOT NULL,
            city        NVARCHAR(40),
            district    NVARCHAR(60),
            open_date   DATE,
            status      NVARCHAR(10),
            CONSTRAINT pk_mis_dim_branch PRIMARY KEY (branch_id),
            CONSTRAINT uq_mis_dim_branch_code UNIQUE (branch_code),
            CONSTRAINT fk_mis_dim_branch_region FOREIGN KEY (region_id)
                REFERENCES MIS_DIM_REGION (region_id)
        ) DISTRIBUTE ON (branch_id)""",
    "MIS_DIM_ADVISOR": """
        CREATE TABLE MIS_DIM_ADVISOR (
            advisor_id   INTEGER NOT NULL,
            advisor_code NVARCHAR(10)  NOT NULL,
            first_name   NVARCHAR(40),
            last_name    NVARCHAR(60),
            branch_id    INTEGER NOT NULL,
            role         NVARCHAR(40),
            hire_date    DATE,
            status       NVARCHAR(10),
            CONSTRAINT pk_mis_dim_advisor PRIMARY KEY (advisor_id),
            CONSTRAINT uq_mis_dim_advisor_code UNIQUE (advisor_code),
            CONSTRAINT fk_mis_dim_advisor_branch FOREIGN KEY (branch_id)
                REFERENCES MIS_DIM_BRANCH (branch_id)
        ) DISTRIBUTE ON (advisor_id)""",
    "MIS_DIM_PRODUCT": """
        CREATE TABLE MIS_DIM_PRODUCT (
            product_id          INTEGER NOT NULL,
            product_code        NVARCHAR(20) NOT NULL,
            product_name        NVARCHAR(80) NOT NULL,
            product_group       NVARCHAR(40) NOT NULL,
            product_subgroup    NVARCHAR(60),
            is_balance_product  SMALLINT NOT NULL,
            min_amount          NUMERIC(14, 2),
            max_amount          NUMERIC(14, 2),
            commission_rate     NUMERIC(6, 4),
            CONSTRAINT pk_mis_dim_product PRIMARY KEY (product_id),
            CONSTRAINT uq_mis_dim_product_code UNIQUE (product_code)
        ) DISTRIBUTE ON (product_id)""",
    "MIS_DIM_CHANNEL": """
        CREATE TABLE MIS_DIM_CHANNEL (
            channel_id   INTEGER NOT NULL,
            channel_name NVARCHAR(40) NOT NULL,
            CONSTRAINT pk_mis_dim_channel PRIMARY KEY (channel_id),
            CONSTRAINT uq_mis_dim_channel_name UNIQUE (channel_name)
        ) DISTRIBUTE ON (channel_id)""",
    "MIS_DIM_CAMPAIGN": """
        CREATE TABLE MIS_DIM_CAMPAIGN (
            campaign_id    INTEGER NOT NULL,
            campaign_code  NVARCHAR(20) NOT NULL,
            campaign_name  NVARCHAR(120) NOT NULL,
            campaign_type  NVARCHAR(40),
            target_segment NVARCHAR(60),
            product_id     INTEGER,
            start_date     DATE,
            end_date       DATE,
            budget         NUMERIC(14, 2),
            CONSTRAINT pk_mis_dim_campaign PRIMARY KEY (campaign_id),
            CONSTRAINT uq_mis_dim_campaign_code UNIQUE (campaign_code),
            CONSTRAINT fk_mis_dim_campaign_product FOREIGN KEY (product_id)
                REFERENCES MIS_DIM_PRODUCT (product_id),
            CONSTRAINT fk_mis_dim_campaign_start_date FOREIGN KEY (start_date)
                REFERENCES MIS_DIM_DATE (calendar_date),
            CONSTRAINT fk_mis_dim_campaign_end_date FOREIGN KEY (end_date)
                REFERENCES MIS_DIM_DATE (calendar_date)
        ) DISTRIBUTE ON (campaign_id)""",
    "MIS_DIM_DATE": """
        CREATE TABLE MIS_DIM_DATE (
            calendar_date          DATE NOT NULL,
            month_start            DATE NOT NULL,
            month_end              DATE NOT NULL,
            day_of_month           SMALLINT NOT NULL,
            is_business_day        SMALLINT NOT NULL,
            business_day_of_month  SMALLINT NOT NULL,
            business_days_in_month SMALLINT NOT NULL,
            CONSTRAINT pk_mis_dim_date PRIMARY KEY (calendar_date)
        ) DISTRIBUTE ON (calendar_date)""",
    "MIS_DIM_ORG_ASSIGNMENT": """
        CREATE TABLE MIS_DIM_ORG_ASSIGNMENT (
            org_assignment_sk INTEGER NOT NULL,
            advisor_id        INTEGER NOT NULL,
            branch_id         INTEGER NOT NULL,
            region_id         INTEGER NOT NULL,
            valid_from        DATE NOT NULL,
            valid_to          DATE NOT NULL,
            is_current        SMALLINT NOT NULL,
            change_reason     NVARCHAR(60),
            CONSTRAINT pk_mis_dim_org_assignment PRIMARY KEY (org_assignment_sk),
            CONSTRAINT uq_mis_dim_org_assignment_start UNIQUE (advisor_id, valid_from),
            CONSTRAINT fk_mis_dim_org_assignment_advisor FOREIGN KEY (advisor_id)
                REFERENCES MIS_DIM_ADVISOR (advisor_id),
            CONSTRAINT fk_mis_dim_org_assignment_branch FOREIGN KEY (branch_id)
                REFERENCES MIS_DIM_BRANCH (branch_id),
            CONSTRAINT fk_mis_dim_org_assignment_region FOREIGN KEY (region_id)
                REFERENCES MIS_DIM_REGION (region_id)
        ) DISTRIBUTE ON (advisor_id)""",
    "MIS_FACT_SALES": """
        CREATE TABLE MIS_FACT_SALES (
            sale_id      INTEGER NOT NULL,
            sale_date    DATE NOT NULL,
            branch_id    INTEGER NOT NULL,
            advisor_id   INTEGER NOT NULL,
            product_id   INTEGER NOT NULL,
            channel_id   INTEGER NOT NULL,
            customer_id  INTEGER NOT NULL,
            amount       NUMERIC(14, 2),
            commission   NUMERIC(12, 2),
            sale_status  NVARCHAR(10) NOT NULL,
            CONSTRAINT pk_mis_fact_sales PRIMARY KEY (sale_id),
            CONSTRAINT fk_mis_fact_sales_branch FOREIGN KEY (branch_id)
                REFERENCES MIS_DIM_BRANCH (branch_id),
            CONSTRAINT fk_mis_fact_sales_advisor FOREIGN KEY (advisor_id)
                REFERENCES MIS_DIM_ADVISOR (advisor_id),
            CONSTRAINT fk_mis_fact_sales_product FOREIGN KEY (product_id)
                REFERENCES MIS_DIM_PRODUCT (product_id),
            CONSTRAINT fk_mis_fact_sales_channel FOREIGN KEY (channel_id)
                REFERENCES MIS_DIM_CHANNEL (channel_id),
            CONSTRAINT fk_mis_fact_sales_date FOREIGN KEY (sale_date)
                REFERENCES MIS_DIM_DATE (calendar_date)
        ) DISTRIBUTE ON RANDOM""",
    "MIS_FACT_BALANCES": """
        CREATE TABLE MIS_FACT_BALANCES (
            balance_month DATE NOT NULL,
            branch_id     INTEGER NOT NULL,
            product_id    INTEGER NOT NULL,
            account_count INTEGER,
            balance_amount NUMERIC(16, 2),
            CONSTRAINT pk_mis_fact_balances PRIMARY KEY (balance_month, branch_id, product_id),
            CONSTRAINT fk_mis_fact_balances_branch FOREIGN KEY (branch_id)
                REFERENCES MIS_DIM_BRANCH (branch_id),
            CONSTRAINT fk_mis_fact_balances_product FOREIGN KEY (product_id)
                REFERENCES MIS_DIM_PRODUCT (product_id),
            CONSTRAINT fk_mis_fact_balances_date FOREIGN KEY (balance_month)
                REFERENCES MIS_DIM_DATE (calendar_date)
        ) DISTRIBUTE ON RANDOM""",
    "MIS_FACT_CUSTOMER_MOVEMENT": """
        CREATE TABLE MIS_FACT_CUSTOMER_MOVEMENT (
            movement_month   DATE NOT NULL,
            branch_id        INTEGER NOT NULL,
            customers_start  INTEGER,
            customers_new    INTEGER,
            customers_lost   INTEGER,
            customers_end    INTEGER,
            product_relations INTEGER,
            customers_2plus  INTEGER,
            customers_3plus  INTEGER,
            customers_4plus  INTEGER,
            CONSTRAINT pk_mis_fact_customer_movement PRIMARY KEY (movement_month, branch_id),
            CONSTRAINT fk_mis_fact_customer_movement_branch FOREIGN KEY (branch_id)
                REFERENCES MIS_DIM_BRANCH (branch_id),
            CONSTRAINT fk_mis_fact_customer_movement_date FOREIGN KEY (movement_month)
                REFERENCES MIS_DIM_DATE (calendar_date)
        ) DISTRIBUTE ON RANDOM""",
    "MIS_FACT_CAMPAIGN_RESULTS": """
        CREATE TABLE MIS_FACT_CAMPAIGN_RESULTS (
            campaign_id  INTEGER NOT NULL,
            branch_id    INTEGER NOT NULL,
            contacts     INTEGER,
            responses    INTEGER,
            conversions  INTEGER,
            sales_amount NUMERIC(14, 2),
            cost         NUMERIC(12, 2),
            CONSTRAINT pk_mis_fact_campaign_results PRIMARY KEY (campaign_id, branch_id),
            CONSTRAINT fk_mis_fact_campaign_results_campaign FOREIGN KEY (campaign_id)
                REFERENCES MIS_DIM_CAMPAIGN (campaign_id),
            CONSTRAINT fk_mis_fact_campaign_results_branch FOREIGN KEY (branch_id)
                REFERENCES MIS_DIM_BRANCH (branch_id)
        ) DISTRIBUTE ON RANDOM""",
    "MIS_FACT_BRANCH_PLAN": """
        CREATE TABLE MIS_FACT_BRANCH_PLAN (
            branch_id   INTEGER NOT NULL,
            plan_month  DATE NOT NULL,
            plan_amount NUMERIC(14, 2),
            CONSTRAINT pk_mis_fact_branch_plan PRIMARY KEY (branch_id, plan_month),
            CONSTRAINT fk_mis_fact_branch_plan_branch FOREIGN KEY (branch_id)
                REFERENCES MIS_DIM_BRANCH (branch_id),
            CONSTRAINT fk_mis_fact_branch_plan_date FOREIGN KEY (plan_month)
                REFERENCES MIS_DIM_DATE (calendar_date)
        ) DISTRIBUTE ON (branch_id)""",
    "MIS_FACT_ADVISOR_PERF": """
        CREATE TABLE MIS_FACT_ADVISOR_PERF (
            advisor_id       INTEGER NOT NULL,
            perf_month       DATE NOT NULL,
            plan_amount      NUMERIC(14, 2),
            sales_score      INTEGER,
            conversion_score INTEGER,
            quality_score    INTEGER,
            activity_score   INTEGER,
            rating           SMALLINT,
            note             NVARCHAR(60),
            CONSTRAINT pk_mis_fact_advisor_perf PRIMARY KEY (advisor_id, perf_month),
            CONSTRAINT fk_mis_fact_advisor_perf_advisor FOREIGN KEY (advisor_id)
                REFERENCES MIS_DIM_ADVISOR (advisor_id),
            CONSTRAINT fk_mis_fact_advisor_perf_date FOREIGN KEY (perf_month)
                REFERENCES MIS_DIM_DATE (calendar_date)
        ) DISTRIBUTE ON (advisor_id)""",
    "MIS_DIM_USER": """
        CREATE TABLE MIS_DIM_USER (
            user_id      INTEGER NOT NULL,
            user_code    NVARCHAR(20) NOT NULL,
            display_name NVARCHAR(80),
            role         NVARCHAR(20) NOT NULL,
            advisor_id   INTEGER,
            branch_id    INTEGER,
            region_id    INTEGER,
            CONSTRAINT pk_mis_dim_user PRIMARY KEY (user_id),
            CONSTRAINT uq_mis_dim_user_code UNIQUE (user_code),
            CONSTRAINT fk_mis_dim_user_advisor FOREIGN KEY (advisor_id)
                REFERENCES MIS_DIM_ADVISOR (advisor_id),
            CONSTRAINT fk_mis_dim_user_branch FOREIGN KEY (branch_id)
                REFERENCES MIS_DIM_BRANCH (branch_id),
            CONSTRAINT fk_mis_dim_user_region FOREIGN KEY (region_id)
                REFERENCES MIS_DIM_REGION (region_id)
        ) DISTRIBUTE ON (user_id)""",
    "MIS_FACT_PERFORMANCE_SNAPSHOT": """
        CREATE TABLE MIS_FACT_PERFORMANCE_SNAPSHOT (
            snapshot_date        DATE NOT NULL,
            month_start          DATE NOT NULL,
            advisor_id           INTEGER NOT NULL,
            historical_branch_id INTEGER NOT NULL,
            historical_region_id INTEGER NOT NULL,
            sales_count_day      INTEGER,
            sales_amount_day     NUMERIC(16, 2),
            commission_day       NUMERIC(14, 2),
            cancellations_day    INTEGER,
            cancellation_amount_day NUMERIC(16, 2),
            customers_day        INTEGER,
            sales_count_mtd      INTEGER,
            sales_amount_mtd     NUMERIC(16, 2),
            commission_mtd       NUMERIC(14, 2),
            cancellations_mtd    INTEGER,
            cancellation_amount_mtd NUMERIC(16, 2),
            customers_mtd        INTEGER,
            plan_amount          NUMERIC(16, 2),
            quality_score        INTEGER,
            activity_score       INTEGER,
            CONSTRAINT pk_mis_fact_performance_snapshot PRIMARY KEY
                (snapshot_date, advisor_id, historical_branch_id),
            CONSTRAINT fk_mis_fact_performance_snapshot_date FOREIGN KEY (snapshot_date)
                REFERENCES MIS_DIM_DATE (calendar_date),
            CONSTRAINT fk_mis_fact_performance_snapshot_month FOREIGN KEY (month_start)
                REFERENCES MIS_DIM_DATE (calendar_date),
            CONSTRAINT fk_mis_fact_performance_snapshot_advisor FOREIGN KEY (advisor_id)
                REFERENCES MIS_DIM_ADVISOR (advisor_id),
            CONSTRAINT fk_mis_fact_performance_snapshot_branch FOREIGN KEY (historical_branch_id)
                REFERENCES MIS_DIM_BRANCH (branch_id),
            CONSTRAINT fk_mis_fact_performance_snapshot_region FOREIGN KEY (historical_region_id)
                REFERENCES MIS_DIM_REGION (region_id),
            CONSTRAINT fk_mis_fact_performance_snapshot_perf FOREIGN KEY (advisor_id, month_start)
                REFERENCES MIS_FACT_ADVISOR_PERF (advisor_id, perf_month)
        ) DISTRIBUTE ON (snapshot_date)""",
    "MIS_AUDIT_SNAPSHOT_LOAD": """
        CREATE TABLE MIS_AUDIT_SNAPSHOT_LOAD (
            load_id             NVARCHAR(30) NOT NULL,
            snapshot_date       DATE NOT NULL,
            loaded_at           NVARCHAR(30) NOT NULL,
            source_max_date     DATE NOT NULL,
            snapshot_rows       INTEGER NOT NULL,
            source_booked_amount NUMERIC(18, 2),
            mart_booked_amount   NUMERIC(18, 2),
            difference_amount    NUMERIC(18, 2),
            status              NVARCHAR(10) NOT NULL,
            CONSTRAINT pk_mis_audit_snapshot_load PRIMARY KEY (snapshot_date),
            CONSTRAINT uq_mis_audit_snapshot_load_id UNIQUE (load_id),
            CONSTRAINT fk_mis_audit_snapshot_load_date FOREIGN KEY (snapshot_date)
                REFERENCES MIS_DIM_DATE (calendar_date),
            CONSTRAINT fk_mis_audit_snapshot_load_source_date FOREIGN KEY (source_max_date)
                REFERENCES MIS_DIM_DATE (calendar_date)
        ) DISTRIBUTE ON (snapshot_date)""",
    "MIS_CONTROL_DATASET_LOAD": """
        CREATE TABLE MIS_CONTROL_DATASET_LOAD (
            dataset_name   NVARCHAR(64) NOT NULL,
            version_no     BIGINT NOT NULL,
            load_id        NVARCHAR(64) NOT NULL,
            source_max_date DATE,
            published_at   NVARCHAR(30) NOT NULL,
            status         NVARCHAR(16) NOT NULL,
            row_count      BIGINT,
            checksum       NVARCHAR(128),
            CONSTRAINT pk_mis_control_dataset_load PRIMARY KEY
                (dataset_name, version_no),
            CONSTRAINT uq_mis_control_dataset_load_id UNIQUE (load_id)
        ) DISTRIBUTE ON RANDOM""",
}

LOAD_COLUMNS = {
    "MIS_DIM_REGION": [("region_id", "INTEGER"), ("region_code", "NVARCHAR(10)"),
                       ("region_name", "NVARCHAR(60)"), ("area_name", "NVARCHAR(60)"),
                       ("seat_city", "NVARCHAR(40)")],
    "MIS_DIM_BRANCH": [("branch_id", "INTEGER"), ("branch_code", "NVARCHAR(10)"),
                       ("branch_name", "NVARCHAR(80)"), ("region_id", "INTEGER"),
                       ("city", "NVARCHAR(40)"), ("district", "NVARCHAR(60)"),
                       ("open_date", "DATE"), ("status", "NVARCHAR(10)")],
    "MIS_DIM_ADVISOR": [("advisor_id", "INTEGER"), ("advisor_code", "NVARCHAR(10)"),
                        ("first_name", "NVARCHAR(40)"), ("last_name", "NVARCHAR(60)"),
                        ("branch_id", "INTEGER"), ("role", "NVARCHAR(40)"),
                        ("hire_date", "DATE"), ("status", "NVARCHAR(10)")],
    "MIS_DIM_PRODUCT": [("product_id", "INTEGER"), ("product_code", "NVARCHAR(20)"),
                        ("product_name", "NVARCHAR(80)"), ("product_group", "NVARCHAR(40)"),
                        ("product_subgroup", "NVARCHAR(60)"), ("is_balance_product", "SMALLINT"),
                        ("min_amount", "NUMERIC(14, 2)"), ("max_amount", "NUMERIC(14, 2)"),
                        ("commission_rate", "NUMERIC(6, 4)")],
    "MIS_DIM_CHANNEL": [("channel_id", "INTEGER"), ("channel_name", "NVARCHAR(40)")],
    "MIS_DIM_CAMPAIGN": [("campaign_id", "INTEGER"), ("campaign_code", "NVARCHAR(20)"),
                         ("campaign_name", "NVARCHAR(120)"), ("campaign_type", "NVARCHAR(40)"),
                         ("target_segment", "NVARCHAR(60)"), ("product_id", "INTEGER"),
                         ("start_date", "DATE"), ("end_date", "DATE"), ("budget", "NUMERIC(14, 2)")],
    "MIS_DIM_DATE": [("calendar_date", "DATE"), ("month_start", "DATE"),
                     ("month_end", "DATE"), ("day_of_month", "SMALLINT"),
                     ("is_business_day", "SMALLINT"),
                     ("business_day_of_month", "SMALLINT"),
                     ("business_days_in_month", "SMALLINT")],
    "MIS_DIM_ORG_ASSIGNMENT": [("org_assignment_sk", "INTEGER"),
                               ("advisor_id", "INTEGER"), ("branch_id", "INTEGER"),
                               ("region_id", "INTEGER"), ("valid_from", "DATE"),
                               ("valid_to", "DATE"), ("is_current", "SMALLINT"),
                               ("change_reason", "NVARCHAR(60)")],
    "MIS_FACT_SALES": [("sale_id", "INTEGER"), ("sale_date", "DATE"), ("branch_id", "INTEGER"),
                       ("advisor_id", "INTEGER"), ("product_id", "INTEGER"),
                       ("channel_id", "INTEGER"), ("customer_id", "INTEGER"),
                       ("amount", "NUMERIC(14, 2)"), ("commission", "NUMERIC(12, 2)"),
                       ("sale_status", "NVARCHAR(10)")],
    "MIS_FACT_BALANCES": [("balance_month", "DATE"), ("branch_id", "INTEGER"),
                          ("product_id", "INTEGER"), ("account_count", "INTEGER"),
                          ("balance_amount", "NUMERIC(16, 2)")],
    "MIS_FACT_CUSTOMER_MOVEMENT": [("movement_month", "DATE"), ("branch_id", "INTEGER"),
                                   ("customers_start", "INTEGER"), ("customers_new", "INTEGER"),
                                   ("customers_lost", "INTEGER"), ("customers_end", "INTEGER"),
                                   ("product_relations", "INTEGER"), ("customers_2plus", "INTEGER"),
                                   ("customers_3plus", "INTEGER"), ("customers_4plus", "INTEGER")],
    "MIS_FACT_CAMPAIGN_RESULTS": [("campaign_id", "INTEGER"), ("branch_id", "INTEGER"),
                                  ("contacts", "INTEGER"), ("responses", "INTEGER"),
                                  ("conversions", "INTEGER"), ("sales_amount", "NUMERIC(14, 2)"),
                                  ("cost", "NUMERIC(12, 2)")],
    "MIS_FACT_BRANCH_PLAN": [("branch_id", "INTEGER"), ("plan_month", "DATE"),
                             ("plan_amount", "NUMERIC(14, 2)")],
    "MIS_FACT_ADVISOR_PERF": [("advisor_id", "INTEGER"), ("perf_month", "DATE"),
                              ("plan_amount", "NUMERIC(14, 2)"), ("sales_score", "INTEGER"),
                              ("conversion_score", "INTEGER"), ("quality_score", "INTEGER"),
                              ("activity_score", "INTEGER"), ("rating", "SMALLINT"),
                              ("note", "NVARCHAR(60)")],
    "MIS_DIM_USER": [("user_id", "INTEGER"), ("user_code", "NVARCHAR(20)"),
                     ("display_name", "NVARCHAR(80)"), ("role", "NVARCHAR(20)"),
                     ("advisor_id", "INTEGER"), ("branch_id", "INTEGER"),
                     ("region_id", "INTEGER")],
    "MIS_FACT_PERFORMANCE_SNAPSHOT": [
        ("snapshot_date", "DATE"), ("month_start", "DATE"),
        ("advisor_id", "INTEGER"), ("historical_branch_id", "INTEGER"),
        ("historical_region_id", "INTEGER"), ("sales_count_day", "INTEGER"),
        ("sales_amount_day", "NUMERIC(16, 2)"),
        ("commission_day", "NUMERIC(14, 2)"), ("cancellations_day", "INTEGER"),
        ("cancellation_amount_day", "NUMERIC(16, 2)"), ("customers_day", "INTEGER"),
        ("sales_count_mtd", "INTEGER"), ("sales_amount_mtd", "NUMERIC(16, 2)"),
        ("commission_mtd", "NUMERIC(14, 2)"), ("cancellations_mtd", "INTEGER"),
        ("cancellation_amount_mtd", "NUMERIC(16, 2)"), ("customers_mtd", "INTEGER"),
        ("plan_amount", "NUMERIC(16, 2)"), ("quality_score", "INTEGER"),
        ("activity_score", "INTEGER")],
    "MIS_AUDIT_SNAPSHOT_LOAD": [
        ("load_id", "NVARCHAR(30)"), ("snapshot_date", "DATE"),
        ("loaded_at", "NVARCHAR(30)"), ("source_max_date", "DATE"),
        ("snapshot_rows", "INTEGER"), ("source_booked_amount", "NUMERIC(18, 2)"),
        ("mart_booked_amount", "NUMERIC(18, 2)"),
        ("difference_amount", "NUMERIC(18, 2)"), ("status", "NVARCHAR(10)")],
    "MIS_CONTROL_DATASET_LOAD": [
        ("dataset_name", "NVARCHAR(64)"), ("version_no", "BIGINT"),
        ("load_id", "NVARCHAR(64)"), ("source_max_date", "DATE"),
        ("published_at", "NVARCHAR(30)"), ("status", "NVARCHAR(16)"),
        ("row_count", "BIGINT"), ("checksum", "NVARCHAR(128)")],
}

# ---------------------------------------------------------------------------
# Reference data (English names)
# ---------------------------------------------------------------------------

# (region_code, region_name, area_name, seat_city)
REGIONS = [
    ("RNOR", "North Region", "Belfast Area", "Belfast"),
    ("RWES", "West Region", "Galway Area", "Galway"),
    ("RCEN", "Central Region", "Dublin Area", "Dublin"),
    ("RSWE", "South-West Region", "Cork Area", "Cork"),
    ("RSOU", "South Region", "Limerick Area", "Limerick"),
    ("RSEA", "South-East Region", "Waterford Area", "Waterford"),
]

# (region_id, branch_code, city, district, weight)
BRANCHES = [
    (1, "BEL01", "Belfast", "City Centre", 1.8), (1, "BEL02", "Belfast", "Queen's Quarter", 1.5),
    (1, "BEL03", "Belfast", "Ballyhackamore", 1.1), (1, "DRY01", "Derry", "City Centre", 1.0),
    (1, "NWY01", "Newry", "City Centre", 0.8), (1, "BAL01", "Ballymena", "Town Centre", 0.7),
    (1, "ENN01", "Enniskillen", "Town Centre", 0.6),
    (2, "GAL01", "Galway", "City Centre", 1.6), (2, "GAL02", "Galway", "Salthill", 1.2),
    (2, "SLI01", "Sligo", "Town Centre", 0.9), (2, "LET01", "Letterkenny", "Town Centre", 0.9),
    (2, "CAS01", "Castlebar", "Town Centre", 0.7), (2, "WES01", "Westport", "Town Centre", 0.6),
    (2, "ATH01", "Athlone", "Town Centre", 1.0),
    (3, "DUB01", "Dublin", "City Centre", 2.6), (3, "DUB02", "Dublin", "Ballsbridge", 2.2),
    (3, "DUB03", "Dublin", "Temple Bar", 2.0), (3, "DUB04", "Dublin", "Rathmines", 1.9),
    (3, "DUB05", "Dublin", "Blanchardstown", 1.8), (3, "SWO01", "Swords", "Town Centre", 1.4),
    (3, "DRO01", "Drogheda", "Town Centre", 1.0), (3, "DUN01", "Dundalk", "Town Centre", 0.9),
    (4, "COR01", "Cork", "City Centre", 2.0), (4, "COR02", "Cork", "Douglas", 1.5),
    (4, "KIL01", "Killarney", "Town Centre", 0.9), (4, "TRA01", "Tralee", "Town Centre", 0.8),
    (4, "MAL01", "Mallow", "Town Centre", 0.6), (4, "BAN01", "Bantry", "Town Centre", 0.5),
    (5, "LIM01", "Limerick", "City Centre", 1.5), (5, "LIM02", "Limerick", "Castletroy", 1.1),
    (5, "KIK01", "Kilkenny", "City Centre", 0.9), (5, "WEX01", "Wexford", "Town Centre", 0.8),
    (5, "CLO01", "Clonmel", "Town Centre", 0.7), (5, "ENN02", "Ennis", "Town Centre", 0.8),
    (6, "WAT01", "Waterford", "City Centre", 1.2), (6, "WAT02", "Waterford", "Dunmore Road", 0.9),
    (6, "CAR01", "Carlow", "Town Centre", 0.8), (6, "TUL01", "Tullamore", "Town Centre", 0.7),
    (6, "MUL01", "Mullingar", "Town Centre", 0.7), (6, "NAA01", "Naas", "Town Centre", 0.8),
    (6, "POR01", "Portlaoise", "Town Centre", 0.7),
]

CHANNELS = [
    "Branch", "Mobile app", "Internet banking", "Call center", "Telemarketing", "External agent",
]

# (channel, [(product_group, weight), ...]) - which products flow through which channel
CHANNEL_MIX = {
    "Branch": [("Loans", 25), ("Insurance", 20), ("Current accounts (ROR)", 15),
               ("Savings accounts", 10), ("Term deposits", 10), ("Investments", 20)],
    "Mobile app": [("Current accounts (ROR)", 35), ("Term deposits", 25),
                   ("Savings accounts", 20), ("Investments", 10),
                   ("Insurance", 5), ("Loans", 5)],
    "Internet banking": [("Term deposits", 30), ("Current accounts (ROR)", 30),
                         ("Savings accounts", 15), ("Investments", 15), ("Loans", 10)],
    "Call center": [("Loans", 30), ("Insurance", 25), ("Investments", 15),
                    ("Current accounts (ROR)", 15), ("Term deposits", 15)],
    "Telemarketing": [("Insurance", 35), ("Loans", 20), ("Term deposits", 20),
                      ("Investments", 15), ("Current accounts (ROR)", 10)],
    "External agent": [("Loans", 35), ("Insurance", 25), ("Investments", 20),
                       ("Current accounts (ROR)", 10), ("Term deposits", 10)],
}
CHANNEL_PROB = [("Branch", 45), ("Mobile app", 20), ("Internet banking", 12),
                ("Call center", 10), ("Telemarketing", 8), ("External agent", 5)]

FLAT_COMMISSION = {"Current accounts (ROR)": (30, 80),
                   "Savings accounts": (20, 60),
                   "Term deposits": (25, 90)}

# Campaign types: (name, reach, response_rate, unit_cost_pln)
CAMPAIGN_TYPES = [
    ("Email", 0.60, 0.08, 0.10),
    ("SMS", 0.50, 0.05, 0.15),
    ("Telemarketing", 0.25, 0.18, 4.50),
    ("Push app", 0.45, 0.12, 0.30),
    ("Direct mail", 0.15, 0.10, 2.50),
]
SEGMENTS = ["Clients 18-30", "Clients 31-45", "Clients 46-60", "Seniors 60+",
            "ROR owners", "Deposit holders", "Loan clients", "New clients"]
CONVERSION_BY_GROUP = {"Loans": 0.12, "Insurance": 0.20, "Current accounts (ROR)": 0.30,
                       "Savings accounts": 0.25, "Term deposits": 0.25, "Investments": 0.08}

FIRST_NAMES = ["Sean", "Liam", "Conor", "Patrick", "Aoife", "Ciara", "Niamh", "Eoin",
               "Cian", "Declan", "Fionn", "Aisling", "Maeve", "Grainne", "Saoirse", "Ronan",
               "Darragh", "Cathal", "Eimear", "Orla", "Colm", "Niall", "Padraig", "Brendan",
               "Fiona", "Sinead", "Roisin", "Eamon", "Cormac", "Dara", "Tadhg", "Rory"]
LAST_NAMES = ["Murphy", "O'Brien", "Kelly", "O'Sullivan", "Walsh", "Smith", "O'Connor",
              "Byrne", "Ryan", "O'Neill", "O'Reilly", "Doyle", "McCarthy", "Gallagher",
              "Kennedy", "Lynch", "Murray", "Quinn", "Moore", "McLoughlin", "Collins",
              "Campbell", "Clarke", "Hughes", "Fitzgerald", "Brown", "Martin", "Maguire",
              "Nolan", "Flynn", "Thompson", "O'Donnell", "Dunne", "Brennan", "Burke",
              "Hayes", "O'Grady", "Hogan", "Keane", "Kavanagh", "O'Shea", "Power",
              "O'Leary", "Fitzpatrick", "Cunningham", "O'Dwyer", "McMahon", "Casey",
              "Sheehan", "Duffy", "O'Keeffe", "Crowley", "Barry", "Healy", "Brady",
              "Carroll", "McCabe", "Doherty", "Callaghan", "Coffey"]
ROLES = [("Client Advisor", 55), ("Investment Advisor", 15), ("Mortgage Advisor", 10),
         ("Junior Advisor", 20)]


@dataclass
class Product:
    code: str
    name: str
    group: str
    subgroup: str
    min_amount: float
    max_amount: float
    commission_rate: float
    weight: int = 10
    balance: dict[str, float] | None = None  # share / start_avg / new_base / churn / drift


# (code, name, group, subgroup, min, max, commission, weight, balance_params|None)
PRODUCTS: list[Product] = [
    Product("KRED_GOT", "Cash loan", "Loans", "Cash loans", 5000, 80000, 0.008, 45, None),
    Product("KRED_HIP", "Mortgage loan", "Loans", "Mortgage loans", 100000, 800000, 0.005, 10, None),
    Product("KRED_KON", "Consolidation loan", "Loans", "Consolidation loans", 20000, 150000, 0.009, 25, None),
    Product("KRED_AUT", "Car loan", "Loans", "Car loans", 15000, 120000, 0.007, 20, None),
    Product("INW_FUN", "Investment funds", "Investments", "Funds", 2000, 100000, 0.015, 35,
            dict(share=0.08, start_avg=12000, new_base=1.0, churn=0.003, drift=0.008)),
    Product("INW_OBL", "Treasury bonds", "Investments", "Bonds", 1000, 50000, 0.006, 30,
            dict(share=0.06, start_avg=18000, new_base=0.8, churn=0.004, drift=0.006)),
    Product("INW_AKC", "Securities account", "Investments", "Equities", 1000, 30000, 0.004, 20, None),
    Product("INW_IKE", "IKE / IKZE pension account", "Investments", "Pension products", 500, 20000, 0.010, 10,
            dict(share=0.03, start_avg=9000, new_base=0.4, churn=0.002, drift=0.009)),
    Product("INW_STR", "Structured products", "Investments", "Structured products", 10000, 150000, 0.020, 5, None),
    Product("UBEZ_ZYCIE", "Life insurance", "Insurance", "Life insurance", 300, 5000, 0.12, 35, None),
    Product("UBEZ_MAJ", "Property insurance", "Insurance", "Property insurance", 200, 3000, 0.10, 25, None),
    Product("UBEZ_PODR", "Travel insurance", "Insurance", "Travel insurance", 50, 500, 0.08, 15, None),
    Product("UBEZ_KRED", "Credit insurance", "Insurance", "Credit insurance", 500, 8000, 0.15, 25, None),
    Product("ROR_STD", "ROR current account", "Current accounts (ROR)", "Standard ROR", 100, 3000, 0.0, 55,
            dict(share=0.62, start_avg=4200, new_base=9.0, churn=0.006, drift=0.004)),
    Product("ROR_MOL", "Youth account", "Current accounts (ROR)", "Youth ROR", 0, 1000, 0.0, 20,
            dict(share=0.10, start_avg=800, new_base=1.5, churn=0.012, drift=0.002)),
    Product("ROR_SEN", "Senior account", "Current accounts (ROR)", "Senior ROR", 0, 2000, 0.0, 15,
            dict(share=0.08, start_avg=9000, new_base=1.2, churn=0.003, drift=0.005)),
    Product("KOS_STD", "Savings account", "Savings accounts", "Standard savings", 0, 5000, 0.0, 55,
            dict(share=0.35, start_avg=8500, new_base=4.0, churn=0.005, drift=0.006)),
    Product("KOS_MAX", "Savings account MAX", "Savings accounts", "Premium savings", 0, 5000, 0.0, 25,
            dict(share=0.12, start_avg=25000, new_base=1.5, churn=0.004, drift=0.008)),
    Product("LOK_STD", "Standard term deposit", "Term deposits", "Standard deposits", 1000, 200000, 0.0, 45,
            dict(share=0.18, start_avg=18000, new_base=2.5, churn=0.020, drift=0.002)),
    Product("LOK_PRO", "Progressive term deposit", "Term deposits", "Progressive deposits", 1000, 150000, 0.0, 20,
            dict(share=0.06, start_avg=15000, new_base=0.8, churn=0.020, drift=0.002)),
    Product("LOK_ONL", "Online term deposit", "Term deposits", "Online deposits", 500, 100000, 0.0, 25,
            dict(share=0.10, start_avg=12000, new_base=1.5, churn=0.025, drift=0.001)),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def months(start: dt.date, end: dt.date) -> list[dt.date]:
    out: list[dt.date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(dt.date(y, m, 1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def month_index(month: dt.date) -> int:
    return (month.year - FIRST_MONTH.year) * 12 + (month.month - FIRST_MONTH.month)


def days_in_month(month: dt.date) -> int:
    if month.month == 12:
        nxt = dt.date(month.year + 1, 1, 1)
    else:
        nxt = dt.date(month.year, month.month + 1, 1)
    return (nxt - month).days


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's Poisson sampler (works for small lambda)."""
    if lam <= 0:
        return 0
    l = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= l:
            return k - 1


def weighted_choice(rng: random.Random, items: list[tuple[Any, float]]) -> Any:
    total = sum(w for _, w in items)
    r = rng.random() * total
    upto = 0.0
    for obj, w in items:
        upto += w
        if r <= upto:
            return obj
    return items[-1][0]


def fmt(n: float) -> str:
    return f"{n:,.2f}"


# ---------------------------------------------------------------------------
# Row generators (deterministic, streamed straight into load_data)
# ---------------------------------------------------------------------------

def gen_regions() -> Iterator[list[Any]]:
    for i, (code, name, area, city) in enumerate(REGIONS, start=1):
        yield [i, code, name, area, city]


def gen_branches() -> Iterator[list[Any]]:
    for i, (region_id, code, city, district, _w) in enumerate(BRANCHES, start=1):
        yield [i, code, f"Branch {city} {district}", region_id, city, district,
               f"{2010 + i % 8:04d}-03-01", "ACTIVE"]


def gen_advisors(rng: random.Random, advisors_by_branch: dict[int, list[list[Any]]]) -> Iterator[list[Any]]:
    advisor_id = 1
    for branch_id, branch in enumerate(BRANCHES, start=1):
        n = 4 + rng.randrange(0, 5)
        for _ in range(n):
            role = weighted_choice(rng, ROLES)
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            hire = dt.date(rng.randrange(2010, 2025), rng.randrange(1, 13), rng.randrange(1, 29))
            status = "INACTIVE" if rng.random() < 0.08 else "ACTIVE"
            row = [advisor_id, f"P{advisor_id:04d}", first, last, branch_id, role,
                   hire.isoformat(), status]
            advisors_by_branch.setdefault(branch_id, []).append(row)
            advisor_id += 1
            yield row


def gen_products() -> Iterator[list[Any]]:
    for i, p in enumerate(PRODUCTS, start=1):
        yield [i, p.code, p.name, p.group, p.subgroup, 1 if p.balance else 0,
               p.min_amount, p.max_amount, p.commission_rate]


def gen_channels() -> Iterator[list[Any]]:
    for i, name in enumerate(CHANNELS, start=1):
        yield [i, name]


def gen_sales(rng: random.Random, scale: int,
              products_by_group: dict[str, list[Product]],
              advisors_by_branch: dict[int, list[list[Any]]]) -> Iterator[list[Any]]:
    sale_id = 1
    channel_by_name = {name: i for i, name in enumerate(CHANNELS, start=1)}
    product_by_code = {p.code: (i, p) for i, p in enumerate(PRODUCTS, start=1)}
    branches = [(i, code, city, district, w) for i, (r, code, city, district, w) in enumerate(BRANCHES, start=1)]
    factor = scale / 100.0

    for month in months(START, END):
        idx = month_index(month)
        season = SEASON[month.month - 1]
        growth = 1.0 + 0.006 * idx
        for branch_id, _code, _city, _district, weight in branches:
            for day in range(1, days_in_month(month) + 1):
                d = dt.date(month.year, month.month, day)
                if d > END:
                    continue
                eligible_advisors = [advisor for advisor in advisors_by_branch[branch_id]
                                     if advisor[6] <= d.isoformat()]
                if not eligible_advisors:
                    continue
                weekend = d.weekday() >= 5
                rate = weight * 5.3 * season * growth * factor * (0.15 if weekend else 1.0)
                n = poisson(rng, rate)
                for _ in range(n):
                    channel_name = weighted_choice(rng, CHANNEL_PROB)
                    group = weighted_choice(rng, CHANNEL_MIX[channel_name])
                    product = weighted_choice(rng, [(p, p.weight) for p in products_by_group[group]])
                    if product.group == "Current accounts (ROR)":
                        amount = round(rng.triangular(100, 3000, 400), 2)
                    else:
                        lo, hi = product.min_amount, product.max_amount
                        amount = round(rng.triangular(lo, hi, lo + 0.30 * (hi - lo)), 2)
                    if product.group in FLAT_COMMISSION:
                        lo, hi = FLAT_COMMISSION[product.group]
                        commission = round(rng.uniform(lo, hi), 2)
                    else:
                        commission = round(amount * product.commission_rate, 2)
                    status = "CANCELLED" if rng.random() < 0.03 else "BOOKED"
                    advisor = rng.choice(eligible_advisors)
                    yield [sale_id, d.isoformat(), branch_id, advisor[0],
                           product_by_code[product.code][0], channel_by_name[channel_name],
                           rng.randint(100000, 149999), amount, commission, status]
                    sale_id += 1


def gen_balances(rng: random.Random) -> Iterator[list[Any]]:
    product_by_code = {p.code: i for i, p in enumerate(PRODUCTS, start=1)}
    branches = [(i, w) for i, (r, code, city, district, w) in enumerate(BRANCHES, start=1)]
    state: dict[tuple[int, str], list[float]] = {}

    for month in months(START, END):
        idx = month_index(month)
        season = SEASON[month.month - 1]
        for branch_id, weight in branches:
            if idx == 0:
                base = int(1500 + weight * 1200 * rng.uniform(0.9, 1.1))
            for p in PRODUCTS:
                if p.balance is None:
                    continue
                b = p.balance
                key = (branch_id, p.code)
                if idx == 0:
                    count = max(1, int(base * b["share"] * rng.uniform(0.85, 1.15)))
                    avg = b["start_avg"] * rng.uniform(0.8, 1.3)
                    state[key] = [float(count), avg]
                else:
                    count, avg = state[key]
                    openings = poisson(rng, weight * b["new_base"] * season)
                    churn = count * b["churn"] * rng.uniform(0.7, 1.3)
                    count = max(1, count + openings - churn)
                    avg = avg * (1 + b["drift"]) * rng.uniform(0.98, 1.02)
                    state[key] = [count, avg]
                yield [month.isoformat(), branch_id, product_by_code[p.code],
                       int(round(count)), round(count * avg, 2)]


def gen_movement(rng: random.Random) -> Iterator[list[Any]]:
    branches = [(i, w) for i, (r, code, city, district, w) in enumerate(BRANCHES, start=1)]
    prev_end: dict[int, int] = {}
    for month in months(START, END):
        idx = month_index(month)
        for branch_id, weight in branches:
            if idx == 0:
                start = int(1500 + weight * 1200 * rng.uniform(0.9, 1.1))
            else:
                start = prev_end[branch_id]
            new = poisson(rng, start * (0.008 + weight * 0.004))
            lost = poisson(rng, start * (0.005 + weight * 0.002))
            end = max(1, start + new - lost)
            prev_end[branch_id] = end
            avg_products = 1.60 + 0.017 * idx + rng.uniform(-0.03, 0.05)
            p2 = min(0.65, 0.38 + 0.003 * idx + rng.uniform(-0.015, 0.015))
            p3 = min(0.40, 0.13 + 0.002 * idx + rng.uniform(-0.010, 0.010))
            p4 = min(0.25, 0.04 + 0.0008 * idx + rng.uniform(-0.005, 0.005))
            yield [month.isoformat(), branch_id, start, new, lost, end,
                   int(end * avg_products), int(end * p2), int(end * p3), int(end * p4)]


RATING_NOTES = {5: "Excellent", 4: "Exceeds expectations", 3: "On track",
                2: "Needs improvement", 1: "At risk"}


def gen_advisor_perf(rng: random.Random,
                     advisors: list[list[Any]],
                     booked_by_advisor: dict[tuple[int, dt.date], float]) -> Iterator[list[Any]]:
    """
    Monthly plans, KPI scores and a 1-5 rating per advisor.

    Plans are deliberately independent of the result they measure.  Each plan
    uses up to three *preceding* months (plus seasonality); this avoids the
    common demo-data anti-pattern where a target leaks the current actual.
    Scores (0-100) may use the completed/current outcome because they are
    observations, not targets.
    """
    def hire_month(hire: str) -> dt.date:
        d = dt.date.fromisoformat(hire)
        return dt.date(d.year, d.month, 1)

    for adv in advisors:
        advisor_id, branch_id, hire = adv[0], adv[4], adv[6]
        # per-advisor talent: some are consistently stronger, some weaker
        talent = rng.uniform(0.85, 1.20)
        fallback_capacity = rng.uniform(18000, 65000)
        for month in months(max(hire_month(hire), START), END):
            actual = booked_by_advisor.get((advisor_id, month), 0.0)
            history = []
            cursor = month
            for _ in range(3):
                cursor = (cursor.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
                previous = booked_by_advisor.get((advisor_id, cursor))
                if previous:
                    history.append(previous)
            baseline = sum(history) / len(history) if history else fallback_capacity
            previous_season = SEASON[(month.month - 2) % 12]
            season_adjustment = SEASON[month.month - 1] / previous_season
            plan = round(baseline * season_adjustment * rng.uniform(0.96, 1.06), 2)
            attainment = (actual / plan * 100) if plan else 100.0
            def score(base: float, lo: float, hi: float) -> int:
                return int(round(min(hi, max(lo, base * talent))))
            sales_score = score(50 + (attainment - 100) * 1.2 + rng.uniform(-8, 8),
                                20, 100)
            conversion_score = score(rng.uniform(55, 95), 40, 100)
            quality_score = score(rng.uniform(50, 95), 35, 100)
            activity_score = score(rng.uniform(55, 100), 45, 100)
            if advisor_id in LEAGUE_SHOWCASE_ADVISOR_IDS:
                # Showcase rows must qualify on quality as well as volume and
                # should look like genuine champions in the score breakdown.
                quality_score = max(90, quality_score)
                activity_score = max(90, activity_score)
            overall = (sales_score * 0.40 + conversion_score * 0.25
                       + quality_score * 0.20 + activity_score * 0.15)
            rating = min(5, max(1, int(round(overall / 20))))
            yield [advisor_id, month.isoformat(), plan, sales_score,
                   conversion_score, quality_score, activity_score,
                   rating, RATING_NOTES[rating]]


def gen_branch_plans(perf_rows: list[list[Any]],
                     branch_of_advisor: dict[int, int]) -> Iterator[list[Any]]:
    """Branch monthly plan = sum of its advisors' plans (consistent by design)."""
    totals: dict[tuple[int, str], float] = {}
    for row in perf_rows:
        advisor_id, month, plan = row[0], row[1], row[2]
        key = (branch_of_advisor[advisor_id], month)
        totals[key] = totals.get(key, 0.0) + plan
    for (branch_id, month), plan in sorted(totals.items()):
        yield [branch_id, month, round(plan, 2)]


def gen_users(advisors: list[list[Any]]) -> Iterator[list[Any]]:
    """
    Legacy role users plus a small set of named personas used by fake LDAP.

    Passwords are intentionally not stored in the warehouse. The in-process
    fake LDAP directory owns credential hashes and links to these user codes.
    """
    user_id = 1
    yield [user_id, "NET01", "Network Analyst", "ANALYST", None, None, None]
    user_id += 1
    for region_id, (code, _name, area, _city) in enumerate(REGIONS, start=1):
        yield [user_id, f"AM_{code}", f"{area} Manager", "AREA_MANAGER",
               None, None, region_id]
        user_id += 1
    for branch_id, (region_id, code, city, district, _w) in enumerate(BRANCHES, start=1):
        yield [user_id, f"BM_{code}", f"Branch {city} {district} Manager",
               "BRANCH_MANAGER", None, branch_id, region_id]
        user_id += 1
    for adv in advisors:
        if adv[7] != "ACTIVE":
            continue
        yield [user_id, f"ADV_{adv[1]}", f"{adv[2]} {adv[3]}", "ADVISOR",
               adv[0], adv[4], None]
        user_id += 1

    advisor = next((row for row in advisors if row[1] == "P0001"), advisors[0])
    branch_region_by_id = {
        branch_id: region_id
        for branch_id, (region_id, _code, _city, _district, _weight)
        in enumerate(BRANCHES, start=1)
    }
    demo_users = [
        ["MIS_SQL_DEV01", "MIS SQL Developer", "MIS_SQL_DEVELOPER", None, None, None],
        ["NET_HEAD01", "Head of Sales Network", "NETWORK_HEAD", None, None, None],
        ["REG_DIR_RNOR", "North Region Director", "REGIONAL_DIRECTOR", None, None, 1],
        ["BR_DIR_BEL01", "Belfast Branch Director", "BRANCH_DIRECTOR", None, 1, 1],
        ["ADV_DEMO_P0001", "Customer Advisor P0001", "CUSTOMER_ADVISOR",
         advisor[0], advisor[4], branch_region_by_id[advisor[4]]],
        ["HQ_FULL01", "HQ Full Access", "HQ_FULL_ACCESS", None, None, None],
        ["APP_TESTER01", "Application Developer / Tester", "APP_TESTER", None, None, None],
    ]
    for code, name, role, advisor_id, branch_id, region_id in demo_users:
        yield [user_id, code, name, role, advisor_id, branch_id, region_id]
        user_id += 1


def gen_dates() -> Iterator[list[Any]]:
    """Calendar rows through the true data cut-off, including weekends."""
    day = START
    while day <= END:
        month_start = day.replace(day=1)
        month_end = (month_start.replace(day=28) + dt.timedelta(days=4)).replace(day=1) \
            - dt.timedelta(days=1)
        elapsed = sum(1 for n in range(1, day.day + 1)
                      if dt.date(day.year, day.month, n).weekday() < 5)
        total = sum(1 for n in range(1, month_end.day + 1)
                    if dt.date(day.year, day.month, n).weekday() < 5)
        yield [day.isoformat(), month_start.isoformat(), month_end.isoformat(),
               day.day, 1 if day.weekday() < 5 else 0, elapsed, total]
        day += dt.timedelta(days=1)


def gen_org_assignments(advisors: list[list[Any]]) -> list[list[Any]]:
    """Small but visible SCD2 history used to teach point-in-time joins."""
    region_by_branch = {i: row[0] for i, row in enumerate(BRANCHES, start=1)}
    rows: list[list[Any]] = []
    sk = 1
    for advisor in advisors:
        advisor_id, current_branch = advisor[0], advisor[4]
        if advisor_id % 37 == 0:
            change = dt.date(2026, 8, 8) if advisor_id == 37 else dt.date(2026, 4, 1)
            previous_branch = current_branch - 1 if current_branch > 1 else 2
            rows.append([sk, advisor_id, previous_branch, region_by_branch[previous_branch],
                         START.isoformat(), (change - dt.timedelta(days=1)).isoformat(),
                         0, "Internal transfer"])
            sk += 1
            rows.append([sk, advisor_id, current_branch, region_by_branch[current_branch],
                         change.isoformat(), "9999-12-31", 1, "Internal transfer"])
        else:
            rows.append([sk, advisor_id, current_branch, region_by_branch[current_branch],
                         START.isoformat(), "9999-12-31", 1, "Initial assignment"])
        sk += 1
    return rows


def apply_historical_assignments(sales_rows: list[list[Any]],
                                 assignments: list[list[Any]]) -> None:
    """Stamp the event with the branch valid on its transaction date."""
    by_advisor: dict[int, list[list[Any]]] = {}
    for row in assignments:
        by_advisor.setdefault(row[1], []).append(row)
    for sale in sales_rows:
        sale_date = sale[1]
        for assignment in by_advisor[sale[3]]:
            if assignment[4] <= sale_date <= assignment[5]:
                sale[2] = assignment[2]
                break


def ensure_current_mtd_coverage(sales_rows: list[list[Any]],
                                advisors_by_branch: dict[int, list[list[Any]]]) -> None:
    """Keep even the smallest fixture useful on the 15-Aug dashboard."""
    current_month = END.isoformat()[:7]
    covered = {row[2] for row in sales_rows
               if row[9] == "BOOKED" and row[1].startswith(current_month)}
    next_id = max((row[0] for row in sales_rows), default=0) + 1
    for branch_id in range(1, len(BRANCHES) + 1):
        if branch_id in covered:
            continue
        advisor = advisors_by_branch[branch_id][0]
        amount = round(1000 + branch_id * 37.5, 2)
        # Use the canonical cut-off date so a transfer during the month is
        # stamped to the advisor's assignment valid at the selected snapshot.
        sales_rows.append([next_id, END.isoformat(), branch_id, advisor[0],
                           1, 1, 140000 + branch_id, amount,
                           round(amount * PRODUCTS[0].commission_rate, 2), "BOOKED"])
        next_id += 1


def ensure_league_showcase_qualifiers(
        sales_rows: list[list[Any]], advisors: list[list[Any]]) -> None:
    """Guarantee visible but guardrail-compliant MTD league qualifiers.

    The normal sales generator is scaled for fast fixtures. At low scales it
    is useful for report tests, but it produces too few sales for a leaderboard
    whose eligibility threshold is ten booked sales. Add only the deficit for
    six stable advisors, spread across several branches. Their records remain
    ordinary booked sales and are stamped through the same SCD2 assignment
    process as every other event.
    """
    advisor_by_id = {row[0]: row for row in advisors}
    missing = set(LEAGUE_SHOWCASE_ADVISOR_IDS) - set(advisor_by_id)
    if missing:
        raise ValueError(f"league showcase advisors are missing: {sorted(missing)}")

    current_month = END.isoformat()[:7]
    end_date = END.isoformat()
    booked_counts = {
        advisor_id: sum(
            1 for row in sales_rows
            if row[3] == advisor_id
            and row[9] == "BOOKED"
            and row[1][:7] == current_month
            and row[1] <= end_date)
        for advisor_id in LEAGUE_SHOWCASE_ADVISOR_IDS
    }
    next_id = max((row[0] for row in sales_rows), default=0) + 1
    for advisor_id in LEAGUE_SHOWCASE_ADVISOR_IDS:
        advisor = advisor_by_id[advisor_id]
        hire_date = advisor[6]
        if hire_date > end_date:
            raise ValueError(f"league showcase advisor {advisor_id} is not hired by {END}")
        deficit = max(0, LEAGUE_MIN_BOOKED_SALES - booked_counts[advisor_id])
        for offset in range(deficit):
            sale_date = END - dt.timedelta(days=offset)
            amount = round(LEAGUE_SHOWCASE_SALE_AMOUNT + (advisor_id % 5) * 750, 2)
            sales_rows.append([
                next_id, sale_date.isoformat(), advisor[4], advisor_id,
                1, 1, 160_000 + advisor_id * 100 + offset,
                amount, round(amount * PRODUCTS[0].commission_rate, 2), "BOOKED",
            ])
            next_id += 1


def gen_performance_snapshots(
    sales_rows: list[list[Any]],
    perf_rows: list[list[Any]],
) -> tuple[list[list[Any]], list[list[Any]]]:
    """Reference implementation of the SQL accumulating-snapshot mart.

    Production-style SQL is kept in ``sql/reporting_mart.sql``.  This Python
    equivalent makes the seeded database and database-free tests identical.
    """
    region_by_branch = {i: row[0] for i, row in enumerate(BRANCHES, start=1)}
    perf = {(r[0], r[1][:7]): r for r in perf_rows}
    daily: dict[tuple[str, int, int], dict[str, Any]] = {}
    for sale in sales_rows:
        key = (sale[1], sale[3], sale[2])
        item = daily.setdefault(key, {"count": 0, "amount": 0.0, "commission": 0.0,
                                      "cancel_count": 0, "cancel_amount": 0.0,
                                      "customers": set()})
        if sale[9] == "BOOKED":
            item["count"] += 1
            item["amount"] += sale[7]
            item["commission"] += sale[8]
            item["customers"].add(sale[6])
        else:
            item["cancel_count"] += 1
            item["cancel_amount"] += sale[7]

    daily_by_date: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
    for (sale_date, advisor_id, branch_id), values in daily.items():
        daily_by_date.setdefault(sale_date, []).append((advisor_id, branch_id, values))

    out: list[list[Any]] = []
    audit: list[list[Any]] = []
    running: dict[tuple[int, int], dict[str, Any]] = {}
    source_running = 0.0
    day = START
    active_month = ""
    while day <= END:
        iso, ym = day.isoformat(), day.isoformat()[:7]
        if ym != active_month:
            running = {}
            source_running = 0.0
            active_month = ym
        for advisor_id, branch_id, values in daily_by_date.get(iso, []):
            item = running.setdefault((advisor_id, branch_id), {
                "count": 0, "amount": 0.0, "commission": 0.0,
                "cancel_count": 0, "cancel_amount": 0.0, "customers": set()})
            item["count"] += values["count"]
            item["amount"] += values["amount"]
            item["commission"] += values["commission"]
            item["cancel_count"] += values["cancel_count"]
            item["cancel_amount"] += values["cancel_amount"]
            item["customers"].update(values["customers"])
            source_running += values["amount"]

        day_rows: list[list[Any]] = []
        for (advisor_id, branch_id), accumulated in sorted(running.items()):
            today = daily.get((iso, advisor_id, branch_id), {})
            performance = perf.get((advisor_id, ym), [None] * 9)
            day_rows.append([
                iso, f"{ym}-01", advisor_id, branch_id, region_by_branch[branch_id],
                today.get("count", 0), round(today.get("amount", 0.0), 2),
                round(today.get("commission", 0.0), 2), today.get("cancel_count", 0),
                round(today.get("cancel_amount", 0.0), 2), len(today.get("customers", set())),
                accumulated["count"], round(accumulated["amount"], 2),
                round(accumulated["commission"], 2), accumulated["cancel_count"],
                round(accumulated["cancel_amount"], 2), len(accumulated["customers"]),
                performance[2], performance[5], performance[6],
            ])
        out.extend(day_rows)
        source_amount = round(source_running, 2)
        mart_amount = round(sum(r[12] for r in day_rows), 2)
        difference = round(mart_amount - source_amount, 2)
        audit.append([
            f"SNAP-{day.strftime('%Y%m%d')}", iso, f"{iso}T06:00:00", iso,
            len(day_rows), source_amount, mart_amount, difference,
            "PASS" if difference == 0 else "FAIL",
        ])
        day += dt.timedelta(days=1)
    return out, audit


def gen_campaigns(rng: random.Random) -> tuple[Iterator[list[Any]], list[dict[str, Any]]]:
    all_months = months(START, END)
    campaigns: list[dict[str, Any]] = []
    for i in range(40):
        ctype_name, reach, resp_rate, unit_cost = rng.choice(CAMPAIGN_TYPES)
        segment = rng.choice(SEGMENTS)
        product = rng.choice(PRODUCTS)
        start_month = all_months[rng.randrange(0, len(all_months) - 6)]
        start = start_month + dt.timedelta(days=rng.randrange(0, 15))
        end = start + dt.timedelta(days=rng.randrange(28, 61))
        campaigns.append({
            "id": i + 1,
            "code": f"CMP{i + 1:03d}",
            "name": f"{ctype_name} · {product.name} · {segment}",
            "type": ctype_name,
            "segment": segment,
            "product": product,
            "reach": reach,
            "resp_rate": resp_rate,
            "unit_cost": unit_cost,
            "start": start,
            "end": end,
        })
    return (yield_campaign_rows(campaigns), campaigns)


def yield_campaign_rows(campaigns: list[dict[str, Any]]) -> Iterator[list[Any]]:
    product_ids = {p.code: i for i, p in enumerate(PRODUCTS, start=1)}
    for c in campaigns:
        yield [c["id"], c["code"], c["name"], c["type"], c["segment"],
               product_ids[c["product"].code], c["start"].isoformat(),
               c["end"].isoformat(), 0.0]  # budget filled below


def gen_campaign_results(rng: random.Random, campaigns: list[dict[str, Any]]) -> Iterator[list[Any]]:
    branches = [(i, w) for i, (r, code, city, district, w) in enumerate(BRANCHES, start=1)]
    month_of = lambda d: dt.date(d.year, d.month, 1)
    for c in campaigns:
        idx = month_index(month_of(c["start"]))
        conv_rate = CONVERSION_BY_GROUP[c["product"].group] * rng.uniform(0.6, 1.4)
        avg_amount = (c["product"].min_amount + c["product"].max_amount) / 2.0
        total_cost = 0.0
        for branch_id, weight in branches:
            base = (1500 + weight * 1200) * (1 + 0.008 * idx)
            contacts = max(0, int(base * c["reach"] * rng.uniform(0.9, 1.1)))
            responses = int(contacts * c["resp_rate"] * rng.uniform(0.7, 1.3))
            conversions = int(responses * conv_rate)
            sales_amount = conversions * avg_amount * rng.uniform(0.8, 1.2)
            cost = contacts * c["unit_cost"]
            total_cost += cost
            yield [c["id"], branch_id, contacts, responses, conversions,
                   round(sales_amount, 2), round(cost, 2)]
        c["budget"] = round(total_cost, 2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def table_exists(cur: Any, name: str) -> bool:
    cur.execute("SELECT COUNT(*) FROM _v_table WHERE tablename = ? AND objtype = 'TABLE'", (name,))
    return cur.fetchone()[0] > 0


def create_schema(conn: Any) -> None:
    cur = conn.cursor()
    # Drop children first; once the DDL declares foreign keys, dropping a
    # referenced parent before its dependants is rejected by Netezza.
    for name in reversed(TABLES):
        if table_exists(cur, name):
            cur.execute(f"DROP TABLE {name}")
            print(f"  dropped {name}")
    for name in TABLES:
        cur.execute(TABLE_DDL[name].strip())
        print(f"  created {name}")
    conn.commit()


def load_table(conn: Any, name: str, rows: Iterator[list[Any]]) -> int:
    n = conn.load_data(
        table_name=name,
        rows=rows,
        columns=LOAD_COLUMNS[name],
        encoding="UTF8",
        create_if_missing=False,
    )
    return n


def verify(conn: Any) -> None:
    cur = conn.cursor()
    print("\nVerification:")
    for name in TABLES:
        cur.execute(f"SELECT COUNT(*) FROM {name}")
        print(f"  {name:<32} {cur.fetchone()[0]:>9,} rows")
    cur.execute("SELECT MIN(sale_date), MAX(sale_date), COUNT(*) FROM MIS_FACT_SALES WHERE sale_status = 'BOOKED'")
    print("  booked sales range:", cur.fetchone())
    cur.execute("""SELECT p.product_group, COUNT(*), ROUND(SUM(f.amount), 2)
                   FROM MIS_FACT_SALES f JOIN MIS_DIM_PRODUCT p ON p.product_id = f.product_id
                   WHERE f.sale_status = 'BOOKED' GROUP BY 1 ORDER BY 3 DESC""")
    print("  sales by product group:")
    for row in cur.fetchall():
        print(f"    {row[0]:<24} {row[1]:>8}  {fmt(row[2]):>16} EUR")


def build_dataset(scale: int = 1) -> dict[str, tuple[list[str], list[list[Any]]]]:
    """
    Generate the full MIS dataset in memory — no database involved.

    Uses exactly the same generators (and RNG sequence) as the database seed,
    so `python seed.py` and the in-memory dataset are identical by
    construction. This is what the report-engine tests run against.

    Returns {table_name: (column_names, rows)} with rows as plain Python
    values (dates as ISO strings), ready for an in-memory repository.
    """
    def cols(name: str) -> list[str]:
        return [c[0] for c in LOAD_COLUMNS[name]]

    rng = random.Random(42)
    advisors_by_branch: dict[int, list[list[Any]]] = {}

    tables: dict[str, tuple[list[str], list[list[Any]]]] = {
        "MIS_DIM_REGION": (cols("MIS_DIM_REGION"), list(gen_regions())),
        "MIS_DIM_BRANCH": (cols("MIS_DIM_BRANCH"), list(gen_branches())),
        "MIS_DIM_ADVISOR": (cols("MIS_DIM_ADVISOR"),
                            list(gen_advisors(rng, advisors_by_branch))),
        "MIS_DIM_PRODUCT": (cols("MIS_DIM_PRODUCT"), list(gen_products())),
        "MIS_DIM_CHANNEL": (cols("MIS_DIM_CHANNEL"), list(gen_channels())),
        "MIS_DIM_DATE": (cols("MIS_DIM_DATE"), list(gen_dates())),
    }
    org_rows = gen_org_assignments(tables["MIS_DIM_ADVISOR"][1])
    tables["MIS_DIM_ORG_ASSIGNMENT"] = (
        cols("MIS_DIM_ORG_ASSIGNMENT"), org_rows)

    # campaigns first (their generator also yields the campaign list), then the
    # results which compute per-campaign budgets, then bake budgets into rows
    camp_iter, campaigns = gen_campaigns(rng)
    camp_rows = list(camp_iter)
    results = list(gen_campaign_results(random.Random(2026), campaigns))
    for c, row in zip(campaigns, camp_rows):
        row[-1] = c["budget"]  # budget is the last column
    tables["MIS_DIM_CAMPAIGN"] = (cols("MIS_DIM_CAMPAIGN"), camp_rows)
    tables["MIS_FACT_CAMPAIGN_RESULTS"] = (cols("MIS_FACT_CAMPAIGN_RESULTS"), results)

    products_by_group: dict[str, list[Product]] = {}
    for p in PRODUCTS:
        products_by_group.setdefault(p.group, []).append(p)
    sales_rows = list(gen_sales(random.Random(7), scale, products_by_group,
                                advisors_by_branch))
    ensure_current_mtd_coverage(sales_rows, advisors_by_branch)
    ensure_league_showcase_qualifiers(sales_rows, tables["MIS_DIM_ADVISOR"][1])
    # Stamp generated events after adding coverage and showcase rows. Otherwise
    # a pre-transfer event could make the current branch appear covered even
    # though temporal attribution correctly moves it to the old branch.
    apply_historical_assignments(sales_rows, org_rows)
    tables["MIS_FACT_SALES"] = (cols("MIS_FACT_SALES"), sales_rows)

    # booked monthly totals per branch and per advisor -> plans + performance
    scols = cols("MIS_FACT_SALES")
    bi, ai, di, si, ami = (scols.index("branch_id"), scols.index("advisor_id"),
                           scols.index("sale_date"), scols.index("sale_status"),
                           scols.index("amount"))
    booked_branch: dict[tuple[int, dt.date], float] = {}
    booked_advisor: dict[tuple[int, dt.date], float] = {}
    for r in sales_rows:
        if r[si] != "BOOKED":
            continue
        m = dt.date.fromisoformat(r[di]).replace(day=1)
        booked_branch[(r[bi], m)] = booked_branch.get((r[bi], m), 0.0) + r[ami]
        booked_advisor[(r[ai], m)] = booked_advisor.get((r[ai], m), 0.0) + r[ami]

    advisor_rows = tables["MIS_DIM_ADVISOR"][1]
    perf_rows = list(gen_advisor_perf(random.Random(99), advisor_rows,
                                      booked_advisor))
    tables["MIS_FACT_ADVISOR_PERF"] = (cols("MIS_FACT_ADVISOR_PERF"), perf_rows)
    branch_of_advisor = {r[0]: r[4] for r in advisor_rows}
    tables["MIS_FACT_BRANCH_PLAN"] = (
        cols("MIS_FACT_BRANCH_PLAN"),
        list(gen_branch_plans(perf_rows, branch_of_advisor)),
    )
    tables["MIS_FACT_BALANCES"] = (
        cols("MIS_FACT_BALANCES"), list(gen_balances(random.Random(11))))
    tables["MIS_FACT_CUSTOMER_MOVEMENT"] = (
        cols("MIS_FACT_CUSTOMER_MOVEMENT"), list(gen_movement(random.Random(23))))
    tables["MIS_DIM_USER"] = (cols("MIS_DIM_USER"), list(gen_users(advisor_rows)))
    snapshot_rows, audit_rows = gen_performance_snapshots(sales_rows, perf_rows)
    tables["MIS_FACT_PERFORMANCE_SNAPSHOT"] = (
        cols("MIS_FACT_PERFORMANCE_SNAPSHOT"), snapshot_rows)
    tables["MIS_AUDIT_SNAPSHOT_LOAD"] = (
        cols("MIS_AUDIT_SNAPSHOT_LOAD"), audit_rows)
    data_row_count = sum(
        len(rows) for name, (_columns, rows) in tables.items()
        if name != "MIS_CONTROL_DATASET_LOAD"
    )
    tables["MIS_CONTROL_DATASET_LOAD"] = (
        cols("MIS_CONTROL_DATASET_LOAD"),
        [["MIS_DASHBOARD", 1, "SEED-20260815", END.isoformat(),
          f"{END.isoformat()}T06:00:00", "PUBLISHED", data_row_count, None]],
    )
    return tables


def main() -> int:
    scale = 100
    if "--rows" in sys.argv:
        try:
            scale = int(sys.argv[sys.argv.index("--rows") + 1])
        except (IndexError, ValueError):
            print("Usage: python seed.py [--rows SCALE]")
            return 2
    if scale <= 0:
        print("--rows must be > 0")
        return 2

    print(f"Connecting to {NZ['host']}:{NZ['port']}/{NZ['database']} as {NZ['user']} ...")
    t0 = time.time()
    conn = nzpy.connect(**NZ)
    try:
        print("Recreating schema ...")
        create_schema(conn)

        dataset = build_dataset(scale)
        print(f"Loaded {len(dataset)} tables in memory ({sum(len(r) for _, r in dataset.values()):,} rows)")
        for name, (columns, rows) in dataset.items():
            n = load_table(conn, name, iter(rows))
            print(f"  {name}: {n:,} rows")

        verify(conn)
    finally:
        conn.close()

    print(f"\nDone in {time.time() - t0:.1f}s. Start the dashboard with:  python main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
