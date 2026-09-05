"""
test_c_python_parity_integration.py
====================================
Integration-level parity tests that verify C extension and pure-Python
implementations return identical results for real database queries.

Each test case is run twice: once with C extension enabled and once
with it disabled (via monkeypatch). The results are compared for strict
equality of types and values.

Run:
    pytest tests/test_c_python_parity_integration.py -v
"""

import datetime
from decimal import Decimal

import pytest

import nzpy_extended as nzpy
from nzpy_extended import Interval

pytestmark = pytest.mark.full

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CEXT_ORIGINAL_FLAG = None


@pytest.fixture
def is_c_extension_available():
    import nzpy_extended._cstate as _cstate
    return getattr(_cstate, "_HAVE_C_EXT", False)


@pytest.fixture(params=[pytest.param(True, id="C_ext_enabled"), pytest.param(False, id="pure_python")])
def cext_mode(request, monkeypatch):
    import nzpy_extended._cstate as _cstate

    global _CEXT_ORIGINAL_FLAG
    if _CEXT_ORIGINAL_FLAG is None:
        _CEXT_ORIGINAL_FLAG = getattr(_cstate, "_HAVE_C_EXT", False)

    use_c_ext = request.param
    monkeypatch.setattr(_cstate, "_HAVE_C_EXT", use_c_ext)
    yield use_c_ext
    monkeypatch.setattr(_cstate, "_HAVE_C_EXT", _CEXT_ORIGINAL_FLAG)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


async def _fetch_one(conn, sql):
    cur = conn.cursor()
    try:
        await cur.execute(sql)
        return await cur.fetchone()
    finally:
        await cur.close()


async def _fetch_all(conn, sql):
    cur = conn.cursor()
    try:
        await cur.execute(sql)
        return await cur.fetchall()
    finally:
        await cur.close()


# ---------------------------------------------------------------------------
# Integer types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("sql,expected_type,expected_val", [
    ("SELECT 15::BYTEINT", int, 15),
    ("SELECT -128::BYTEINT", int, -128),
    ("SELECT 127::BYTEINT", int, 127),
    ("SELECT 32767::SMALLINT", int, 32767),
    ("SELECT -32768::SMALLINT", int, -32768),
    ("SELECT 2147483647::INTEGER", int, 2147483647),
    ("SELECT -2147483648::INTEGER", int, -2147483648),
    ("SELECT 9223372036854775807::BIGINT", int, 9223372036854775807),
    ("SELECT -9223372036854775808::BIGINT", int, -9223372036854775808),
    ("SELECT 0::INTEGER", int, 0),
])
async def test_integer_type(con_cext, cext_mode, sql, expected_type, expected_val):
    row = await _fetch_one(con_cext, sql)
    assert row is not None
    val = row[0]
    assert isinstance(val, expected_type), f"Expected {expected_type}, got {type(val).__name__} [{cext_mode}]"
    assert val == expected_val, f"Expected {expected_val}, got {val} [{cext_mode}]"


# ---------------------------------------------------------------------------
# Float / Double types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("sql,expected_type", [
    ("SELECT 3.14::FLOAT", float),
    ("SELECT 3.14159265358979::DOUBLE PRECISION", float),
    ("SELECT 0.0::FLOAT", float),
    ("SELECT 0.0::DOUBLE PRECISION", float),
])
async def test_float_type(con_cext, cext_mode, sql, expected_type):
    row = await _fetch_one(con_cext, sql)
    assert row is not None
    val = row[0]
    assert isinstance(val, expected_type), f"Expected {expected_type}, got {type(val).__name__} [{cext_mode}]"
    assert val is not None


# ---------------------------------------------------------------------------
# NUMERIC / DECIMAL types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("sql,expected_str", [
    ("SELECT 12345.6789::NUMERIC(10,4)", "12345.6789"),
    ("SELECT 0.00::NUMERIC(10,2)", "0.00"),
    ("SELECT 12345678901234.5678::NUMERIC(20,4)", "12345678901234.5678"),
    ("SELECT -123.45::NUMERIC(10,2)", "-123.45"),
])
async def test_numeric_type(con_cext, cext_mode, sql, expected_str):
    row = await _fetch_one(con_cext, sql)
    assert row is not None
    val = row[0]
    assert isinstance(val, Decimal), f"Expected Decimal, got {type(val).__name__} [{cext_mode}]"
    assert str(val) == expected_str, f"Expected {expected_str}, got {val} [{cext_mode}]"


# ---------------------------------------------------------------------------
# Date / Time / Timestamp types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("sql,expected_type", [
    ("SELECT '2024-12-11'::DATE", datetime.date),
    ("SELECT '2000-01-01'::DATE", datetime.date),
    ("SELECT '2024-02-29'::DATE", datetime.date),  # leap year
    ("SELECT '05:41:15'::TIME", datetime.time),
    ("SELECT '00:00:00'::TIME", datetime.time),
    ("SELECT '23:59:59'::TIME", datetime.time),
    (
        "SELECT '12:00:00-12'::TIMETZ "
        "FROM JUST_DATA..DIMACCOUNT LIMIT 1",
        str,
    ),
    ("SELECT '2024-12-11 14:30:00'::TIMESTAMP", datetime.datetime),
    ("SELECT '2000-01-01 00:00:00'::TIMESTAMP", datetime.datetime),
])
async def test_date_time_type(con_cext, cext_mode, sql, expected_type):
    row = await _fetch_one(con_cext, sql)
    assert row is not None
    val = row[0]
    assert isinstance(val, expected_type), f"Expected {expected_type}, got {type(val).__name__} [{cext_mode}]"
    assert val is not None


# ---------------------------------------------------------------------------
# Boolean types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("sql,expected", [
    ("SELECT true::BOOLEAN", True),
    ("SELECT false::BOOLEAN", False),
    ("SELECT 1=1", True),
    ("SELECT 1=0", False),
])
async def test_bool_type(con_cext, cext_mode, sql, expected):
    row = await _fetch_one(con_cext, sql)
    assert row is not None
    val = row[0]
    assert isinstance(val, bool), f"Expected bool, got {type(val).__name__} [{cext_mode}]"
    assert val is expected, f"Expected {expected}, got {val} [{cext_mode}]"


# ---------------------------------------------------------------------------
# String types (CHAR, VARCHAR, NCHAR, NVARCHAR)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("sql,expected", [
    ("SELECT 'hello'::VARCHAR(10)", "hello"),
    ("SELECT 'abc'::CHAR(10)", "abc       "),  # char is space-padded
    ("SELECT 'abc'::NCHAR(10)", "abc       "),     # nchar is space-padded
    ("SELECT 'abc'::NVARCHAR(10)", "abc"),
    ("SELECT ''::VARCHAR(10)", ""),
])
async def test_string_type(con_cext, cext_mode, sql, expected):
    row = await _fetch_one(con_cext, sql)
    assert row is not None
    val = row[0]
    assert isinstance(val, str), f"Expected str, got {type(val).__name__} [{cext_mode}]"
    assert val == expected, f"Expected {expected!r}, got {val!r} [{cext_mode}]"


# ---------------------------------------------------------------------------
# Unicode / special character tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("sql,expected_substring", [
    ("SELECT '中文测试'::NVARCHAR(50)", "中文测试"),
    ("SELECT 'café'::VARCHAR(50)", "café"),
    ("SELECT 'zażółć'::NVARCHAR(50)", "zażółć"),
])
async def test_unicode_string(con_cext, cext_mode, sql, expected_substring):
    row = await _fetch_one(con_cext, sql)
    assert row is not None
    val = row[0]
    assert expected_substring in val, f"Expected {expected_substring} in {val!r} [{cext_mode}]"


# ---------------------------------------------------------------------------
# NULL handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", [
    "SELECT NULL",
    "SELECT NULL::INTEGER",
    "SELECT NULL::VARCHAR(10)",
    "SELECT NULL::BOOLEAN",
    "SELECT NULL::DATE",
    "SELECT NULL::TIMESTAMP",
    "SELECT NULL::NUMERIC(10,2)",
])
async def test_null_handling(con_cext, cext_mode, sql):
    row = await _fetch_one(con_cext, sql)
    assert row is not None
    val = row[0]
    assert val is None, f"Expected None, got {val!r} [{cext_mode}]"


# ---------------------------------------------------------------------------
# Multi-column consistency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_column(con_cext, cext_mode):
    sql = "SELECT 1::INTEGER, 3.14::DOUBLE, 'abc'::VARCHAR(10), NULL, true::BOOLEAN"
    row = await _fetch_one(con_cext, sql)
    assert row is not None
    assert len(row) == 5

    assert isinstance(row[0], int)
    assert isinstance(row[1], float)
    assert isinstance(row[2], str)
    assert row[3] is None
    assert isinstance(row[4], bool)

    assert row[0] == 1
    assert abs(row[1] - 3.14) < 0.001
    assert row[2] == "abc"
    assert row[4] is True


# ---------------------------------------------------------------------------
# Multi-row consistency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_row_consistency(con_cext, cext_mode):
    # Create temp table, insert, select, compare
    cur = con_cext.cursor()
    try:
        await cur.execute(
            "CREATE TEMP TABLE IF NOT EXISTS parity_test "
            "(id INT, name VARCHAR(50), val NUMERIC(10,2))"
        )
        await cur.execute("DELETE FROM parity_test")
        await cur.execute("INSERT INTO parity_test VALUES (1, 'alpha', 10.50)")
        await cur.execute("INSERT INTO parity_test VALUES (2, 'beta', 20.75)")
        await cur.execute("INSERT INTO parity_test VALUES (3, 'gamma', NULL)")
        await cur.execute("SELECT id, name, val FROM parity_test ORDER BY id")
        rows = await cur.fetchall()

        assert len(rows) == 3
        assert rows[0] == [1, "alpha", Decimal("10.50")]
        assert rows[1] == [2, "beta", Decimal("20.75")]
        assert rows[2] == [3, "gamma", None]

        for row in rows:
            assert isinstance(row[0], int)
            assert isinstance(row[1], str)
            if row[2] is not None:
                assert isinstance(row[2], Decimal)
    finally:
        try:
            await cur.execute("DROP TABLE parity_test")
        except Exception:
            pass
        await cur.close()


# ---------------------------------------------------------------------------
# Processed vs literal queries (cross-check)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processed_table_vs_literal(con_cext, cext_mode):
    row_direct = await _fetch_one(
        con_cext,
        "SELECT 1::INTEGER, 'abc'::VARCHAR(10), 3.14::DOUBLE, true::BOOLEAN",
    )
    assert row_direct is not None
    assert row_direct[0] == 1
    assert row_direct[1] == "abc"
    assert abs(row_direct[2] - 3.14) < 0.001
    assert row_direct[3] is True


# ---------------------------------------------------------------------------
# Interval type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interval_type(con_cext, cext_mode):
    row = await _fetch_one(con_cext, "SELECT INTERVAL '2:30:00' HOUR TO SECOND")
    assert row is not None
    val = row[0]
    assert isinstance(val, nzpy.Interval), (
        f"Expected Interval, got {type(val).__name__} [{cext_mode}]"
    )
    assert val.microseconds == 9000000000, (
        f"Expected 9000000000 µs (2.5 h), got {val.microseconds} [{cext_mode}]"
    )
    assert val.months == 0
    assert val.days == 0


# ---------------------------------------------------------------------------
# VarBinary (BYTEA) type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_varbinary_type(con_cext, cext_mode):
    row = await _fetch_one(
        con_cext, "SELECT CAST('hello' AS BYTEA)"
    )
    assert row is not None
    val = row[0]
    assert isinstance(val, (str, bytes)), (
        f"Expected str or bytes, got {type(val).__name__} [{cext_mode}]"
    )


# ---------------------------------------------------------------------------
# BYTEINT negative boundary (parity-critical: signed vs unsigned)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("val", [-1, -128, 0, 1, 127])
async def test_byteint_boundary(con_cext, cext_mode, val):
    row = await _fetch_one(con_cext, f"SELECT {val}::BYTEINT")
    assert row is not None
    result = row[0]
    assert isinstance(result, int), (
        f"Expected int, got {type(result).__name__} [{cext_mode}]"
    )
    assert result == val, (
        f"Expected {val}, got {result} (signedness check) [{cext_mode}]"
    )


# ---------------------------------------------------------------------------
# VarFixedChar type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_varfixedchar_type(con_cext, cext_mode):
    row = await _fetch_one(con_cext, "SELECT 'fixed'::CHAR(10)")
    assert row is not None
    val = row[0]
    assert isinstance(val, str), (
        f"Expected str, got {type(val).__name__} [{cext_mode}]"
    )
    assert val == "fixed     ", (
        f"Expected space-padded char, got {val!r} [{cext_mode}]"
    )
