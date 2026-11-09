"""
test_invalid_sql.py
===================
Python equivalent of InvalidSqlTests.cs from JustyBase.NetezzaDriver.Tests.

Covers:
  - Syntax errors raise nzpy.Error before/during fetch.
  - Runtime cast errors raise nzpy.Error during fetch.
  - Connection remains usable after the error (no session corruption).
"""

import os
import pytest

import nzpy_extended as nzpy

pytestmark = pytest.mark.full


NZ_HOST     = os.environ.get("NZ_DEV_HOST",     "127.0.0.1")
NZ_PORT     = int(os.environ.get("NZ_DEV_PORT",  "5480"))
NZ_DB       = os.environ.get("NZ_DEV_DB",        "JUST_DATA")
NZ_USER     = os.environ.get("NZ_DEV_USER",      "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD",  "password")

CONN_KWARGS = dict(
    user=NZ_USER, password=NZ_PASSWORD,
    host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
)

# ---------------------------------------------------------------------------
# Setup helper: ensure TEST_NUM_TXT exists with a non-numeric row
# ---------------------------------------------------------------------------
_TEST_NUM_TXT_SETUP = [
    "DROP TABLE TEST_NUM_TXT_PY IF EXISTS",
    "CREATE TABLE TEST_NUM_TXT_PY AS SELECT 'X' AS COL DISTRIBUTE ON RANDOM",
    "INSERT INTO TEST_NUM_TXT_PY SELECT '1'",          # mixed numeric/text
]


async def _setup_test_table(cur):
    for sql in _TEST_NUM_TXT_SETUP:
        try:
            await cur.execute(sql)
        except Exception:
            pass  # table may already exist


async def _cleanup_test_table(cur):
    try:
        await cur.execute("DROP TABLE TEST_NUM_TXT_PY IF EXISTS")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Syntax error — execute should raise
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_syntax_error_raises_on_execute():
    """Mirrors ReaderShouldThrow / ExecuteNonQueryShouldThrow."""
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        with pytest.raises(nzpy.Error):
            await cur.execute("SELECT 1,,2")
            await cur.fetchall()

        # Connection must still be usable
        cur2 = conn.cursor()
        await cur2.execute("SELECT CURRENT_CATALOG")
        row = await cur2.fetchone()
        assert row is not None

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_syntax_error_raises_on_nonquery():
    """Mirrors ExecuteNonQueryShouldThrow."""
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        with pytest.raises(nzpy.Error):
            await cur.execute("SELECT 1,,2;SELECT 1,2")

        # Follow-up query works
        cur2 = conn.cursor()
        await cur2.execute("SELECT CURRENT_CATALOG")
        assert (await cur2.fetchone()) is not None

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_syntax_error_raises_on_scalar():
    """Mirrors ExecuteScalarShouldThrow."""
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        with pytest.raises(nzpy.Error):
            await cur.execute("SELECT 1,,2;SELECT 1,2")
            await cur.fetchone()

        cur2 = conn.cursor()
        await cur2.execute("SELECT CURRENT_CATALOG")
        assert (await cur2.fetchone()) is not None

    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Runtime cast errors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT 1/0 FROM JUST_DATA..DIMDATE LIMIT 1",
    "SELECT 'X'::INT",
])
@pytest.mark.asyncio
@pytest.mark.timeout(20)
async def test_runtime_error_raises(sql):
    """Mirrors SqlQueries_WithExpectedExceptions_ShouldThrowException."""
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        error_raised = False
        try:
            await cur.execute(sql)
            while True:
                row = await cur.fetchone()
                if row is None:
                    break
        except nzpy.Error:
            error_raised = True

        assert error_raised, f"Expected nzpy.Error for: {sql}"

        # Session survives
        cur2 = conn.cursor()
        await cur2.execute("SELECT CURRENT_CATALOG")
        assert (await cur2.fetchone()) is not None

    finally:
        await conn.close()


# These queries fail during fetch (server processes table scan before error).
@pytest.mark.parametrize("sql_tmpl", [
    "SELECT 1/0 FROM TEST_NUM_TXT_PY",
    "SELECT 'X'::INT FROM TEST_NUM_TXT_PY",
])
@pytest.mark.asyncio
@pytest.mark.timeout(20)
async def test_runtime_error_with_test_table_immediate(sql_tmpl):
    """
    Mirrors SqlQueries_WithExpectedExceptions_ShouldThrowException.
    These queries produce an error that nzpy_extended raises promptly.
    """
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await _setup_test_table(cur)
        try:
            error_raised = False
            try:
                await cur.execute(sql_tmpl)
                while True:
                    row = await cur.fetchone()
                    if row is None:
                        break
            except nzpy.Error:
                error_raised = True

            assert error_raised, f"Expected nzpy.Error for: {sql_tmpl}"

            # Session survives
            cur2 = conn.cursor()
            await cur2.execute("SELECT CURRENT_CATALOG")
            assert (await cur2.fetchone()) is not None
        finally:
            await _cleanup_test_table(cur)

    finally:
        await conn.close()


# Aggregate/JOIN queries with runtime cast errors — server may delay error
# until spools complete (C# uses 120s command timeout for these).
@pytest.mark.parametrize("sql_tmpl", [
    "SELECT SUM(X.COL::INT) FROM TEST_NUM_TXT_PY X",
    "SELECT * FROM TEST_NUM_TXT_PY X JOIN TEST_NUM_TXT_PY X2 ON X.COL::INT = X2.COL::INT",
])
@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_runtime_error_with_test_table_aggregate(sql_tmpl):
    """
    These queries hang because nzpy_extended does not handle aggregate/JOIN
    error responses correctly (driver-level bug — see skip reason above).
    Mirrors the C# test SqlQueries_WithExpectedExceptions_ShouldThrowException.
    """
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await _setup_test_table(cur)
        try:
            error_raised = False
            try:
                await cur.execute(sql_tmpl)
                while True:
                    row = await cur.fetchone()
                    if row is None:
                        break
            except nzpy.Error:
                error_raised = True
            assert error_raised, f"Expected nzpy.Error for: {sql_tmpl}"
        finally:
            await _cleanup_test_table(cur)
    finally:
        await conn.close()
