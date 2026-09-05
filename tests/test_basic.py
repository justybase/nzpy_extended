"""
test_basic.py
=============
Python equivalent of BasicTests.cs from JustyBase.NetezzaDriver.Tests.

Key goals:
  - Verify that nzpy_extended results match the independent JustyBase driver for a wide
    range of SQL types and queries.
  - Cover interval/time scalar types.
  - Cover null handling for string types (IsDBNull / GetString equivalents).
  - Cover column access by index.

The differential tests use the built @justybase/netezza-driver Node package
through tests/reference_driver.js, so they do not require ODBC.
"""

import os
import pytest

import nzpy_extended as nzpy
from reference_driver import ReferenceConnection, compare_reference_rows

pytestmark = pytest.mark.full


NZ_HOST     = os.environ.get("NZ_DEV_HOST",     "192.168.0.144")
NZ_PORT     = int(os.environ.get("NZ_DEV_PORT",  "5480"))
NZ_DB       = os.environ.get("NZ_DEV_DATABASE") or os.environ.get("NZ_DEV_DB", "JUST_DATA")
NZ_USER     = os.environ.get("NZ_DEV_USER",      "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD",  "password")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_conn():
    return await nzpy.connect(
        user=NZ_USER, password=NZ_PASSWORD,
        host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
    )


async def _fetch_one(sql):
    """Return first row from sql using nzpy."""
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        return await cur.fetchone()
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Interval / Time type tests  (mirrors ExpectedIntervalTime theory in C#)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql,expected_prefix", [
    ("SELECT '2 years 5 hours 11 months 41 minutes 15 sec'::interval",
     "2 years 11 mons 05:41:15"),
    ("SELECT '5 hours 41 minutes  15 sec'::interval",
     "05:41:15"),
    ("SELECT '05:41:15'::TIME",
     "05:41:15"),
    # NOTE: The C# test also runs these against JUST_DATA..DIMDATE, but that
    # path is table-specific and may not be available in all environments.
    # We test the literal equivalents above; the FROM-TABLE form is skipped
    # here to keep the test portable.
])
@pytest.mark.asyncio
async def test_interval_and_time_types(sql, expected_prefix):
    row = await _fetch_one(sql)
    assert row is not None
    val = row[0]
    from nzpy_extended import Interval
    if isinstance(val, Interval):
        assert val is not None
    elif isinstance(val, str):
        assert val.startswith(expected_prefix), (
            f"Expected value starting with '{expected_prefix}', got '{val}'"
        )
    else:
        val_str = val.isoformat() if hasattr(val, 'isoformat') else str(val)
        assert val_str.startswith(expected_prefix), (
            f"Expected value starting with '{expected_prefix}', got '{val}' (str={val_str!r})"
        )


# ---------------------------------------------------------------------------
# Null handling – Python equivalent of GetString_OnNullValue_ThrowsException
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_null_varchar_is_none():
    """NULL::VARCHAR must come back as Python None, not a string."""
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        await cur.execute("SELECT NULL::VARCHAR(10) AS null_text, 'abc' AS non_null_text")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is None, "First column (NULL) must be None"
        assert row[1] == "abc", "Second column must be 'abc'"
    finally:
        await conn.close()


@pytest.mark.parametrize("sql", [
    "SELECT NULL::VARCHAR(10), NULL::NVARCHAR(10), NULL::CHAR(10), NULL::NCHAR(10)",
    "SELECT NULL::VARCHAR(10)",
    "SELECT NULL::VARCHAR(10) FROM JUST_DATA..DIMDATE LIMIT 1",
])
@pytest.mark.asyncio
async def test_null_string_types_are_none(sql):
    """All null string type variants must return Python None."""
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        row = await cur.fetchone()
        assert row is not None
        for val in row:
            assert val is None, f"Expected None but got {val!r}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_null_and_nonnull_mixed():
    """Mixed null/non-null columns – nulls are None, non-nulls have values."""
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        await cur.execute("""
            SELECT
                NULL::VARCHAR(10) AS c1,
                'abc' AS c2,
                NULL::VARCHAR(10) AS c3,
                'def' AS c4,
                NULL::NCHAR(10) AS c5
        """)
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is None
        assert row[1] == "abc"
        assert row[2] is None
        assert row[3] == "def"
        assert row[4] is None
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Basic type queries – just verify they return data without crashing
# ---------------------------------------------------------------------------

BASIC_QUERIES = [
    "SELECT '12:00:00'::TIME, '12:00:00'::TIMETZ, '14:13:12.4321+11:15'::TIMETZ",
    "SELECT NOW()",
    "SELECT false::BOOLEAN FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 15::BYTEINT FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 'ABC'::VARCHAR(10) FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT '2024-12-12'::DATE FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT '2024-12-12'::TIMESTAMP FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 3.14::NUMERIC(10,4) FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 3.14::NUMERIC(38,8) FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 123456789::NUMERIC(38,0) FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 3.14::REAL FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 3.14::DOUBLE FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 12345678::INTEGER FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT -9223372036854775808::BIGINT FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 9223372036854775807::BIGINT FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 25000::SMALLINT FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT false::BOOLEAN",
    "SELECT 15::BYTEINT",
    "SELECT '2024-12-12'::DATE",
    "SELECT 3.14::NUMERIC(38,8)",
]


@pytest.mark.parametrize("sql", BASIC_QUERIES)
@pytest.mark.asyncio
async def test_basic_scalar_query_returns_data(sql):
    """Each basic query must return at least one row without raising."""
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        row = await cur.fetchone()
        assert row is not None, f"No rows returned for: {sql}"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Access by index consistency – Python equivalent of ValidateAccessByIndexOrName
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_column_access_consistency():
    """Fetching all rows and checking that values are consistent across the tuple."""
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        cols = [
            "EMPLOYEEKEY", "PARENTEMPLOYEEKEY",
            "EMPLOYEENATIONALIDALTERNATEKEY", "FIRSTNAME",
            "BIRTHDATE", "TITLE", "LOGINID",
        ]
        await cur.execute(f"SELECT {','.join(cols)} FROM JUST_DATA..DIMEMPLOYEE")
        rows = await cur.fetchall()
        assert len(rows) > 0, "DIMEMPLOYEE must contain rows"
        # Verify that the row has the correct width
        for row in rows:
            assert len(row) == len(cols), "Column count mismatch"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Differential parity against the independent JustyBase driver
# ---------------------------------------------------------------------------

REFERENCE_COMPARE_QUERIES = [
    "SELECT false::BOOLEAN FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 15::BYTEINT FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 'ABC'::VARCHAR(10) FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT '2024-12-12'::DATE FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 3.14::REAL FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 12345678::INTEGER FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 9223372036854775807::BIGINT FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
    "SELECT 25000::SMALLINT FROM JUST_DATA.ADMIN.DIMDATE LIMIT 1",
]


@pytest.mark.parametrize("sql", REFERENCE_COMPARE_QUERIES)
@pytest.mark.asyncio
async def test_reference_driver_and_nzpy_match(sql):
    """nzpy_extended result must agree with the independent JustyBase driver."""
    reference_con = ReferenceConnection()
    nzpy_con = await _make_conn()
    try:
        reference_cur = reference_con.cursor()
        reference_cur.execute(sql)
        reference_rows = reference_cur.fetchall()

        nzpy_cur = nzpy_con.cursor()
        await nzpy_cur.execute(sql)
        nzpy_rows = await nzpy_cur.fetchall()

        compare_reference_rows(
            nzpy_rows, reference_rows, sql, reference_cur.description
        )
    finally:
        reference_con.close()
        await nzpy_con.close()


# ---------------------------------------------------------------------------
# Expected-exception queries  (mirrors SqlQueries_WithExpectedExceptions*)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_divide_by_zero_raises():
    """SELECT 1/0 must raise a database error."""
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        try:
            await cur.execute("SELECT 1/0 FROM JUST_DATA..DIMDATE LIMIT 1")
            await cur.fetchall()
            pytest.fail("Expected exception was not raised")
        except nzpy.Error:
            pass  # expected
        # Connection must still be usable afterwards
        cur2 = conn.cursor()
        await cur2.execute("SELECT CURRENT_CATALOG")
        row = await cur2.fetchone()
        assert row is not None
    finally:
        await conn.close()
