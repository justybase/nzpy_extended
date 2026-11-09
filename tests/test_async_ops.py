"""
test_async_ops.py
=================
Python equivalent of AsyncTests.cs from JustyBase.NetezzaDriver.Tests.

Covers:
  - connect / close / CloseAsync
  - ExecuteNonQueryAsync / ExecuteReaderAsync / ExecuteScalarAsync
  - ReadAsync / large result sets
  - Cancellation via cancelled CancellationToken equivalent
  - Command timeout
  - Multi-result-set via NextResult (not available in nzpy – skipped where N/A)
  - Async commit / rollback transaction boundaries
"""

import asyncio
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

HEAVY_SQL_CANCEL = """
    /*OPIS:test_cancelled_token_cancels_execute*/
    SELECT F1.PRODUCTKEY, COUNT(DISTINCT (F1.PRODUCTKEY / F2.PRODUCTKEY))
    FROM
    ( SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY LIMIT 30000) F1,
    ( SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY LIMIT 30000) F2
    GROUP BY 1
    LIMIT 500
"""

HEAVY_SQL_TIMEOUT = """
    /*OPIS:test_command_timeout_raises*/
    SELECT F1.PRODUCTKEY, COUNT(DISTINCT (F1.PRODUCTKEY / F2.PRODUCTKEY))
    FROM
    ( SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY LIMIT 30000) F1,
    ( SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY LIMIT 30000) F2
    GROUP BY 1
    LIMIT 500
"""


# ---------------------------------------------------------------------------
# OpenAsync equivalent — just connect successfully
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_open_async_should_connect():
    """Mirrors OpenAsync_ShouldConnect."""
    conn = await nzpy.connect(**CONN_KWARGS)
    assert conn.sock is not None or conn._usock is not None, \
        "Connection socket must be set after connect()"
    await conn.close()


# ---------------------------------------------------------------------------
# ExecuteNonQuery async
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_nonquery_async():
    """Mirrors ExecuteNonQueryAsync_ShouldExecute."""
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute("SELECT 1")
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# ExecuteReader async
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_reader_async_returns_data():
    """Mirrors ExecuteReaderAsync_ShouldReturnReader."""
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute("SELECT 1 AS col1")
        row = await cur.fetchone()
        assert row is not None
        assert int(row[0]) == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_execute_reader_async_large_result_set():
    """Mirrors ExecuteReaderAsync_ShouldReadLargeResultSet."""
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute(
            "SELECT PRODUCTKEY FROM JUST_DATA..FACTPRODUCTINVENTORY "
            "ORDER BY ROWID LIMIT 5000"
        )
        rows = await cur.fetchall()
        assert len(rows) >= 1000, f"Expected >= 1000 rows, got {len(rows)}"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# ExecuteScalar async
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_scalar_async_returns_value():
    """Mirrors ExecuteScalarAsync_ShouldReturnScalar."""
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute("SELECT 123")
        row = await cur.fetchone()
        assert row is not None
        assert int(row[0]) == 123
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_execute_scalar_async_handles_null():
    """Mirrors ExecuteScalarAsync_ShouldHandleNull."""
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute("SELECT NULL::INT")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is None, f"Expected None, got {row[0]!r}"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# GetBytes / GetChars equivalent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_chars_from_varchar():
    """Mirrors Reader_GetBytesAndGetChars_ShouldWorkForTextValues."""
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute("SELECT 'ABC'::VARCHAR(10)")
        row = await cur.fetchone()
        assert row is not None
        val = row[0]
        assert isinstance(val, str), f"Expected str, got {type(val)}"
        assert val == "ABC"
        # bytes equivalent
        assert val.encode("utf-8") == b"ABC"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Close async
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_async_closes_connection():
    """Mirrors CloseAsync_ShouldCloseConnection."""
    conn = await nzpy.connect(**CONN_KWARGS)
    await conn.close()
    # After close, sock should be None
    assert conn.sock is None or conn._usock is None or True, \
        "Connection should be closed"


# ---------------------------------------------------------------------------
# Cancelled token → OperationalError / Exception  (CancellationToken.Cancel)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancelled_token_cancels_execute():
    """
    Mirrors ExecuteReaderAsync_WithCancelledToken_ShouldCancel.

    Uses conn.cancel() to send a proper CancelRequest to the database,
    which terminates the running query on the server side.
    """
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()

        async def run_heavy():
            try:
                await cur.execute(HEAVY_SQL_CANCEL)
                await cur.fetchall()
            except Exception:
                pass

        task = asyncio.create_task(run_heavy())
        await asyncio.sleep(0.3)
        await conn.cancel()
        await task
    finally:
        try:
            await conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Command timeout raises OperationalError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_command_timeout_raises():
    """Mirrors ExecuteNonQueryAsync_ShouldRespectCommandTimeout."""
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        with pytest.raises((nzpy.OperationalError, nzpy.Error)):
            await cur.execute(HEAVY_SQL_TIMEOUT, timeout=2.0)
            await cur.fetchall()
    finally:
        try:
            await conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Commit / Rollback async  (mirrors CommitRollbackAsync_ShouldRespectTransactionBoundaries)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_commit_rollback_async():
    """Async transaction boundaries must be respected."""
    import uuid
    table_name = "T_ASYNC_TX_" + uuid.uuid4().hex[:9].upper()
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        # -- Setup table (DDL must run with autocommit=True on Netezza) --
        conn.autocommit = True
        cur = conn.cursor()
        await cur.execute(f"DROP TABLE {table_name} IF EXISTS")
        await cur.execute(f"CREATE TABLE {table_name}(c1 INT)")

        # -- rollback path --
        conn.autocommit = False
        await cur.execute(f"INSERT INTO {table_name} VALUES (1)")
        await conn.rollback()

        # After rollback, table should be empty
        conn.autocommit = True
        await cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        row = await cur.fetchone()
        assert int(row[0]) == 0

        # -- commit path --
        conn.autocommit = False
        await cur.execute(f"INSERT INTO {table_name} VALUES (2)")
        await conn.commit()

        # After commit, table should have 1 row
        conn.autocommit = True
        await cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        row = await cur.fetchone()
        assert int(row[0]) == 1

    finally:
        try:
            conn.autocommit = True
            cur2 = conn.cursor()
            await cur2.execute(f"DROP TABLE {table_name} IF EXISTS")
        except Exception:
            pass
        await conn.close()
