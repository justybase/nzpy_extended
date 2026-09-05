"""
test_type_recognition.py
========================
Schema metadata / type recognition tests — modelled after Node.js
driver's GetSchemaTableTests.test.js, QueryConsistency.test.js, and
the JustyBase reference driver's comparison corpus.

The Python driver's cursor.description returns (name, type_oid) tuples.
We verify that type OIDs match expected Netezza type codes and that
returned Python types are correct for each column type.

Tests cover:
  1. All Netezza type OIDs from cursor.description for table queries
  2. Python type returned for each OID (Decimal for NUMERIC, int for
     BYTEINT/BIGINT/SMALLINT/INTEGER, datetime.date for DATE,
     datetime.datetime for TIMESTAMP, etc.)
  3. Computed column types
  4. Type recognition from both text protocol (no FROM) and binary
     protocol (with FROM table)
  5. NULL values of each type

Reference: Node.js driver's GetSchemaTableTests.test.js and our
type mapping in pg_types dict (core.py).
"""

import datetime
import decimal
import os

import pytest

import nzpy_extended as nzpy

pytestmark = pytest.mark.full

NZ_HOST     = os.environ.get("NZ_DEV_HOST",     "192.168.0.144")
NZ_PORT     = int(os.environ.get("NZ_DEV_PORT",  "5480"))
NZ_DB       = os.environ.get("NZ_DEV_DB",        "JUST_DATA")
NZ_USER     = os.environ.get("NZ_DEV_USER",      "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD",  "password")

TABLE_FROM = "JUST_DATA..DIMDATE"


async def _conn():
    return await nzpy.connect(
        user=NZ_USER, password=NZ_PASSWORD,
        host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
    )


# ---------------------------------------------------------------------------
# Known Netezza type OIDs (from Node.js driver, confirmed with live DB)
# ---------------------------------------------------------------------------

# Standard PostgreSQL OIDs used by Netezza
EXPECTED_OIDS = {
    "BOOLEAN":         16,
    "BYTEA":           17,
    "INT8":            20,
    "INT2":            21,
    "INT4":            23,
    "TEXT":            25,
    "FLOAT4":          700,
    "FLOAT8":          701,
    "BPCHAR":          1042,   # blank-padded char
    "CHAR":            1042,   # Netezza reports ::CHAR as OID 1042 (BPCHAR)
    "VARCHAR":         1043,
    "DATE":            1082,
    "TIME":            1083,
    "TIMESTAMP":       1184,   # Netezza reports TIMESTAMP as TIMESTAMPTZ OID
    "TIMESTAMPTZ":     1184,
    "INTERVAL":        1186,
    "TIMETZ":          1266,
    "NUMERIC":         1700,
    "BYTEINT":         2500,   # Netezza-specific
    "NCHAR":           2522,   # Netezza-specific
    "NVARCHAR":        2530,   # Netezza-specific
}

# Python types expected for each OID
EXPECTED_PYTHON_TYPES = {
    16:    bool,                # BOOLEAN
    20:    int,                 # INT8 (BIGINT)
    21:    int,                 # INT2 (SMALLINT)
    700:   float,               # FLOAT4 (REAL)
    701:   float,               # FLOAT8 (DOUBLE)
    1042:  str,                 # BPCHAR
    1043:  str,                 # VARCHAR
    1082:  datetime.date,       # DATE
    1083:  datetime.time,       # TIME
    1114:  datetime.datetime,   # TIMESTAMP
    1700:  decimal.Decimal,     # NUMERIC
    2500:  int,                 # BYTEINT
    2522:  str,                 # NCHAR
    2530:  str,                 # NVARCHAR
}

# OID 23 (INTEGER) — returned as int by the C extension
# OID 1184 (TIMESTAMPTZ) — returned as string currently (see KNOWN gaps)
# OID 1186 (INTERVAL) — returned as Interval object
# OID 1266 (TIMETZ) — returned as string currently


# ---------------------------------------------------------------------------
# cursor.description OID verification (table queries → binary protocol)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql, expected_oid_name", [
    pytest.param("SELECT true::BOOLEAN",        "BOOLEAN",     id="BOOLEAN"),
    pytest.param("SELECT 12345::BIGINT",        "INT8",        id="BIGINT"),
    pytest.param("SELECT 15::BYTEINT",          "BYTEINT",     id="BYTEINT"),
    pytest.param("SELECT 'abc'::CHAR(10)",      "CHAR",        id="CHAR"),
    pytest.param("SELECT '2024-01-15'::DATE",   "DATE",        id="DATE"),
    pytest.param("SELECT 3.14::FLOAT",          "FLOAT8",      id="DOUBLE"),
    pytest.param("SELECT 3.14::REAL",           "FLOAT4",      id="REAL"),
    pytest.param("SELECT 25000::SMALLINT",      "INT2",        id="SMALLINT"),
    pytest.param("SELECT 12345678::INTEGER",    "INT4",        id="INTEGER"),
    pytest.param("SELECT 123.456::NUMERIC(10,3)", "NUMERIC",   id="NUMERIC"),
    pytest.param("SELECT 'abc'::NVARCHAR(10)",  "NVARCHAR",    id="NVARCHAR"),
    pytest.param("SELECT 'abc'::NCHAR(10)",     "NCHAR",       id="NCHAR"),
    pytest.param("SELECT 'abc'::VARCHAR(10)",   "VARCHAR",     id="VARCHAR"),
    pytest.param("SELECT '12:34:56'::TIME",     "TIME",        id="TIME"),
    pytest.param(
        "SELECT '2024-12-11 14:30:00'::TIMESTAMP", "TIMESTAMP", id="TIMESTAMP"),
])
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_description_oid_text_protocol(sql, expected_oid_name):
    """Verify cursor.description reports correct type OID for
       queries without a FROM clause (text protocol path)."""
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        desc = cur.description
        assert desc is not None, f"No description for: {sql}"
        col_name, col_oid = desc[0][0], desc[0][1]
        expected_oid = EXPECTED_OIDS[expected_oid_name]
        assert col_oid == expected_oid, (
            f"OID mismatch for {expected_oid_name}: "
            f"got {col_oid}, expected {expected_oid}"
        )
    finally:
        await conn.close()


@pytest.mark.parametrize("sql_fragment, expected_oid_name", [
    pytest.param("true::BOOLEAN",           "BOOLEAN",     id="BOOLEAN"),
    pytest.param("12345::BIGINT",           "INT8",        id="BIGINT"),
    pytest.param("15::BYTEINT",             "BYTEINT",     id="BYTEINT"),
    pytest.param("'abc'::CHAR(10)",         "CHAR",        id="CHAR"),
    pytest.param("'2024-01-15'::DATE",      "DATE",        id="DATE"),
    pytest.param("3.14::FLOAT",             "FLOAT8",      id="DOUBLE"),
    pytest.param("3.14::REAL",              "FLOAT4",      id="REAL"),
    pytest.param("25000::SMALLINT",         "INT2",        id="SMALLINT"),
    pytest.param("12345678::INTEGER",       "INT4",        id="INTEGER"),
    pytest.param("123.456::NUMERIC(10,3)",  "NUMERIC",     id="NUMERIC"),
    pytest.param("'abc'::NVARCHAR(10)",     "NVARCHAR",    id="NVARCHAR"),
    pytest.param("'abc'::NCHAR(10)",        "NCHAR",       id="NCHAR"),
    pytest.param("'abc'::VARCHAR(10)",      "VARCHAR",     id="VARCHAR"),
    pytest.param("'12:34:56'::TIME",        "TIME",        id="TIME"),
    pytest.param(
        "'2024-12-11 14:30:00'::TIMESTAMP", "TIMESTAMP",   id="TIMESTAMP"),
])
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_description_oid_table_protocol(sql_fragment, expected_oid_name):
    """Verify cursor.description reports correct type OID for
       queries with a FROM clause (binary / Dbos protocol path)."""
    sql = f"SELECT {sql_fragment} FROM {TABLE_FROM} LIMIT 1"
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        desc = cur.description
        # For Dbos protocol, description may or may not be populated
        # depending on the driver version.  If it is, verify OID.
        if desc is not None:
            col_name, col_oid = desc[0][0], desc[0][1]
            expected_oid = EXPECTED_OIDS[expected_oid_name]
            assert col_oid == expected_oid, (
                f"OID mismatch for {expected_oid_name}: "
                f"got {col_oid}, expected {expected_oid}"
            )
        else:
            pytest.skip(
                "Dbos protocol did not populate description for this query"
            )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Python type returned for each column OID (no FROM — text protocol)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql, expected_type", [
    pytest.param("SELECT 12345::BIGINT",            int,              id="BIGINT"),
    pytest.param("SELECT 15::BYTEINT",              int,              id="BYTEINT"),
    pytest.param("SELECT 25000::SMALLINT",          int,              id="SMALLINT"),
    pytest.param("SELECT 12345678::INTEGER",        int,              id="INTEGER"),
    pytest.param("SELECT 3.14::FLOAT",              float,            id="DOUBLE"),
    pytest.param("SELECT 3.14::REAL",               float,            id="REAL"),
    pytest.param("SELECT 123.456::NUMERIC(10,3)",   decimal.Decimal,  id="NUMERIC"),
    pytest.param("SELECT true::BOOLEAN",            bool,             id="BOOLEAN"),
    pytest.param("SELECT 'abc'::VARCHAR(10)",       str,              id="VARCHAR"),
    pytest.param("SELECT 'abc'::CHAR(10)",          str,              id="CHAR"),
    pytest.param("SELECT 'abc'::NCHAR(10)",         str,              id="NCHAR"),
    pytest.param("SELECT 'abc'::NVARCHAR(10)",      str,              id="NVARCHAR"),
    pytest.param("SELECT '2024-01-15'::DATE",       datetime.date,    id="DATE"),
    pytest.param("SELECT '12:34:56'::TIME",         datetime.time,    id="TIME"),
    pytest.param(
        "SELECT '2024-12-11 14:30:00'::TIMESTAMP",  datetime.datetime, id="TIMESTAMP"),
])
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_python_type_text_protocol(sql, expected_type):
    """Verify returned Python type matches expected type for
       no-FROM (text protocol) queries."""
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        row = await cur.fetchone()
        assert row is not None
        val = row[0]
        assert isinstance(val, expected_type), (
            f"Type mismatch for '{sql}': got {type(val).__name__}, "
            f"expected {expected_type.__name__}"
        )
    finally:
        await conn.close()


@pytest.mark.parametrize("sql_fragment, expected_type", [
    pytest.param("12345::BIGINT",           int,               id="BIGINT"),
    pytest.param("15::BYTEINT",             int,               id="BYTEINT"),
    pytest.param("25000::SMALLINT",         int,               id="SMALLINT"),
    pytest.param("12345678::INTEGER",       int,               id="INTEGER"),
    pytest.param("3.14::FLOAT",             float,             id="DOUBLE"),
    pytest.param("3.14::REAL",              float,             id="REAL"),
    pytest.param("123.456::NUMERIC(10,3)",  decimal.Decimal,   id="NUMERIC"),
    pytest.param("true::BOOLEAN",           bool,              id="BOOLEAN"),
    pytest.param("'abc'::VARCHAR(10)",      str,               id="VARCHAR"),
    pytest.param("'abc'::CHAR(10)",         str,               id="CHAR"),
    pytest.param("'abc'::NCHAR(10)",        str,               id="NCHAR"),
    pytest.param("'abc'::NVARCHAR(10)",     str,               id="NVARCHAR"),
    pytest.param("'2024-01-15'::DATE",      datetime.date,     id="DATE"),
    pytest.param("'12:34:56'::TIME",        datetime.time,     id="TIME"),
    pytest.param(
        "'2024-12-11 14:30:00'::TIMESTAMP", datetime.datetime,  id="TIMESTAMP"),
])
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_python_type_table_protocol(sql_fragment, expected_type):
    """Verify returned Python type matches expected type for
       with-FROM (table / Dbos protocol) queries."""
    sql = f"SELECT {sql_fragment} FROM {TABLE_FROM} LIMIT 1"
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        row = await cur.fetchone()
        assert row is not None
        val = row[0]
        assert isinstance(val, expected_type), (
            f"Type mismatch for '{sql}': got {type(val).__name__}, "
            f"expected {expected_type.__name__}"
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Computed column types
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_computed_columns():
    """Computed columns should return appropriate types.

       Based on Node.js GetSchemaTableTests: computed int → Number,
       computed string → String, computed case → String,
       computed window → BigInt(Number), computed numeric → Number.
    """
    sql = f"""
    SELECT
        1 + 2 AS computed_int,
        'hello' AS computed_string,
        CASE WHEN 1=1 THEN 'yes' ELSE 'no' END AS computed_case,
        ROW_NUMBER() OVER (ORDER BY ROWID) AS computed_window,
        3.14 * 2 AS computed_numeric
    FROM {TABLE_FROM}
    LIMIT 1
    """
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        row = await cur.fetchone()
        assert row is not None
        assert len(row) == 5

        assert isinstance(row[0], int), f"computed_int got {type(row[0]).__name__}"
        assert isinstance(row[1], str), f"computed_string got {type(row[1]).__name__}"
        assert isinstance(row[2], str), f"computed_case got {type(row[2]).__name__}"
        # computed_window — may be int or bigint depending on ROWID type
        assert isinstance(row[3], (int,)), f"computed_window got {type(row[3]).__name__}"
        assert (
            isinstance(row[4], (int, float, decimal.Decimal))
        ), f"computed_numeric got {type(row[4]).__name__}"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# NULL value recognition (each type)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql, expected_oid_name", [
    pytest.param("SELECT NULL::BOOLEAN",        "BOOLEAN",     id="NULL_BOOLEAN"),
    pytest.param("SELECT NULL::BIGINT",         "INT8",        id="NULL_BIGINT"),
    pytest.param("SELECT NULL::BYTEINT",        "BYTEINT",     id="NULL_BYTEINT"),
    pytest.param("SELECT NULL::SMALLINT",       "INT2",        id="NULL_SMALLINT"),
    pytest.param("SELECT NULL::INTEGER",        "INT4",        id="NULL_INTEGER"),
    pytest.param("SELECT NULL::FLOAT",          "FLOAT8",      id="NULL_FLOAT"),
    pytest.param("SELECT NULL::NUMERIC(10,3)",  "NUMERIC",     id="NULL_NUMERIC"),
    pytest.param("SELECT NULL::VARCHAR(10)",    "VARCHAR",     id="NULL_VARCHAR"),
    pytest.param("SELECT NULL::CHAR(10)",       "CHAR",        id="NULL_CHAR"),
    pytest.param("SELECT NULL::NCHAR(10)",      "NCHAR",       id="NULL_NCHAR"),
    pytest.param("SELECT NULL::NVARCHAR(10)",   "NVARCHAR",    id="NULL_NVARCHAR"),
    pytest.param("SELECT NULL::DATE",           "DATE",        id="NULL_DATE"),
    pytest.param("SELECT NULL::TIME",           "TIME",        id="NULL_TIME"),
    pytest.param("SELECT NULL::TIMESTAMP",      "TIMESTAMP",   id="NULL_TIMESTAMP"),
])
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_null_type_oid_correct(sql, expected_oid_name):
    """NULL columns must still report the correct type OID in
       cursor.description."""
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        desc = cur.description
        assert desc is not None
        col_name, col_oid = desc[0][0], desc[0][1]
        expected_oid = EXPECTED_OIDS[expected_oid_name]
        assert col_oid == expected_oid, (
            f"OID mismatch for null {expected_oid_name}: "
            f"got {col_oid}, expected {expected_oid}"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is None, (
            f"Expected NULL for {expected_oid_name}, got {row[0]!r}"
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Multi-column description
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_multi_column_description():
    """A query returning multiple columns of different types must have
       correct OID for each column."""
    sql = """
    SELECT
        1::INTEGER AS col_int,
        'hello'::VARCHAR(10) AS col_varchar,
        3.14::NUMERIC(10,4) AS col_numeric,
        '2024-01-15'::DATE AS col_date,
        true::BOOLEAN AS col_bool
    """
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        desc = cur.description
        assert desc is not None
        assert len(desc) == 5

        expected = [
            ("COL_INT",     EXPECTED_OIDS["INT4"]),
            ("COL_VARCHAR", EXPECTED_OIDS["VARCHAR"]),
            ("COL_NUMERIC", EXPECTED_OIDS["NUMERIC"]),
            ("COL_DATE",    EXPECTED_OIDS["DATE"]),
            ("COL_BOOL",    EXPECTED_OIDS["BOOLEAN"]),
        ]
        for i, (exp_name, exp_oid) in enumerate(expected):
            col_name, col_oid = desc[i][0], desc[i][1]
            col_name_str = col_name.decode() if isinstance(col_name, bytes) else col_name
            assert col_name_str == exp_name, (
                f"Column {i} name: got {col_name_str}, expected {exp_name}"
            )
            assert col_oid == exp_oid, (
                f"Column {i} OID: got {col_oid}, expected {exp_oid}"
            )
    finally:
        await conn.close()
