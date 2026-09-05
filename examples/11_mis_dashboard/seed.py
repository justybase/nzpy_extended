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
    MIS_DIM_USER                - simulated users (analyst, area/branch managers, advisors)

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
END = dt.date(2026, 8, 31)
FIRST_MONTH = dt.date(2024, 1, 1)

SEASON = [0.85, 0.90, 1.05, 1.00, 1.10, 1.00, 0.90, 0.80, 1.10, 1.05, 1.00, 1.15]

TABLES = [
    "MIS_DIM_REGION",
    "MIS_DIM_BRANCH",
    "MIS_DIM_ADVISOR",
    "MIS_DIM_PRODUCT",
    "MIS_DIM_CHANNEL",
    "MIS_DIM_CAMPAIGN",
    "MIS_FACT_SALES",
    "MIS_FACT_BALANCES",
    "MIS_FACT_CUSTOMER_MOVEMENT",
    "MIS_FACT_CAMPAIGN_RESULTS",
    "MIS_FACT_BRANCH_PLAN",
    "MIS_FACT_ADVISOR_PERF",
    "MIS_DIM_USER",
]

TABLE_DDL = {
    "MIS_DIM_REGION": """
        CREATE TABLE MIS_DIM_REGION (
            region_id   INTEGER NOT NULL,
            region_code NVARCHAR(10)  NOT NULL,
            region_name NVARCHAR(60) NOT NULL,
            area_name   NVARCHAR(60),
            seat_city   NVARCHAR(40)
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
            status      NVARCHAR(10)
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
            status       NVARCHAR(10)
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
            commission_rate     NUMERIC(6, 4)
        ) DISTRIBUTE ON (product_id)""",
    "MIS_DIM_CHANNEL": """
        CREATE TABLE MIS_DIM_CHANNEL (
            channel_id   INTEGER NOT NULL,
            channel_name NVARCHAR(40) NOT NULL
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
            budget         NUMERIC(14, 2)
        ) DISTRIBUTE ON (campaign_id)""",
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
            sale_status  NVARCHAR(10) NOT NULL
        ) DISTRIBUTE ON RANDOM""",
    "MIS_FACT_BALANCES": """
        CREATE TABLE MIS_FACT_BALANCES (
            balance_month DATE NOT NULL,
            branch_id     INTEGER NOT NULL,
            product_id    INTEGER NOT NULL,
            account_count INTEGER,
            balance_amount NUMERIC(16, 2)
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
            customers_4plus  INTEGER
        ) DISTRIBUTE ON RANDOM""",
    "MIS_FACT_CAMPAIGN_RESULTS": """
        CREATE TABLE MIS_FACT_CAMPAIGN_RESULTS (
            campaign_id  INTEGER NOT NULL,
            branch_id    INTEGER NOT NULL,
            contacts     INTEGER,
            responses    INTEGER,
            conversions  INTEGER,
            sales_amount NUMERIC(14, 2),
            cost         NUMERIC(12, 2)
        ) DISTRIBUTE ON RANDOM""",
    "MIS_FACT_BRANCH_PLAN": """
        CREATE TABLE MIS_FACT_BRANCH_PLAN (
            branch_id   INTEGER NOT NULL,
            plan_month  DATE NOT NULL,
            plan_amount NUMERIC(14, 2)
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
            note             NVARCHAR(60)
        ) DISTRIBUTE ON (advisor_id)""",
    "MIS_DIM_USER": """
        CREATE TABLE MIS_DIM_USER (
            user_id      INTEGER NOT NULL,
            user_code    NVARCHAR(20) NOT NULL,
            display_name NVARCHAR(80),
            role         NVARCHAR(20) NOT NULL,
            advisor_id   INTEGER,
            branch_id    INTEGER,
            region_id    INTEGER
        ) DISTRIBUTE ON (user_id)""",
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
                    advisor = rng.choice(advisors_by_branch[branch_id])
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

    The monthly plan is the booked sales total for that month perturbed by
    noise, so attainment hovers around 85%-120% (realistic spread). Scores
    (0-100) correlate with attainment; the rating is a weighted blend.
    """
    def hire_month(hire: str) -> dt.date:
        d = dt.date.fromisoformat(hire)
        return dt.date(d.year, d.month, 1)

    for adv in advisors:
        advisor_id, branch_id, hire = adv[0], adv[4], adv[6]
        # per-advisor talent: some are consistently stronger, some weaker
        talent = rng.uniform(0.85, 1.20)
        for month in months(max(hire_month(hire), START), END):
            actual = booked_by_advisor.get((advisor_id, month), 0.0)
            if actual:
                plan = round(actual * rng.uniform(0.80, 1.25), 2)
            else:
                plan = round(rng.uniform(15000, 60000), 2)
            attainment = (actual / plan * 100) if plan else 100.0
            def score(base: float, lo: float, hi: float) -> int:
                return int(round(min(hi, max(lo, base * talent))))
            sales_score = score(50 + (attainment - 100) * 1.2 + rng.uniform(-8, 8),
                                20, 100)
            conversion_score = score(rng.uniform(55, 95), 40, 100)
            quality_score = score(rng.uniform(50, 95), 35, 100)
            activity_score = score(rng.uniform(55, 100), 45, 100)
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
    Simulated users: one analyst, one area manager per region, one branch
    manager per branch, and every active advisor (login as themselves).
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
    for name in TABLES:
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
    }

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