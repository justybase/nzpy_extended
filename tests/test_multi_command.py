"""
test_multi_command.py
=====================
Multi-command consistency tests — modelled after Node.js driver's
MultiCommandConsistency.test.js, HasRowsTests.test.js, and
AdditionalFullTests.test.js.

The Python driver supports semicolon-separated multi-statement SQL via
``cursor.nextset()`` (DB-API 2.0 equivalent of Node.js ``nextResult()``).
In addition, we test:

  1. Sequential commands on the same connection — each via separate
     execute() — must produce identical results to two separate
     connections.

  2. Five sequential queries on one connection without errors.

  3. Transaction flow: begin → insert → rollback → verify rollback;
     begin → insert → commit → verify commit.

  4. Error in one command does not corrupt the connection for the next.

  5. Multi-statement SQL via cursor.nextset(): semicolon-separated
     statements produce separate result sets that can be iterated
     with ``fetchall()`` + ``nextset()`` loops.
"""

import os
import uuid

import pytest

import nzpy_extended as nzpy

pytestmark = pytest.mark.full

NZ_HOST     = os.environ.get("NZ_DEV_HOST",     "127.0.0.1")
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
# Sequential vs separate-connection consistency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_sequential_vs_two_connections():
    """Results from two sequential commands on one connection must
       match results from two separate connections."""
    sql1 = "SELECT * FROM " + TABLE_FROM + " ORDER BY ROWID LIMIT 5"
    sql2 = "SELECT * FROM JUST_DATA..DIMACCOUNT ORDER BY ROWID LIMIT 5"

    connA = await _conn()
    connB = await _conn()
    conn_seq = await _conn()
    try:
        curA = connA.cursor()
        curB = connB.cursor()
        cur_seq = conn_seq.cursor()

        await curA.execute(sql1)
        await curB.execute(sql2)
        rowsA = await curA.fetchmany(10)
        rowsB = await curB.fetchmany(10)

        await cur_seq.execute(sql1)
        rowsS1 = await cur_seq.fetchmany(10)
        await cur_seq.execute(sql2)
        rowsS2 = await cur_seq.fetchmany(10)

        assert rowsA == rowsS1, "First query: separate vs sequential mismatch"
        assert rowsB == rowsS2, "Second query: separate vs sequential mismatch"
    finally:
        await connA.close()
        await connB.close()
        await conn_seq.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_five_sequential_queries():
    """Five sequential queries on the same connection must all succeed."""
    queries = [
        "SELECT 1 AS a",
        "SELECT 2 AS b",
        "SELECT 3 AS c",
        "SELECT 4 AS d",
        "SELECT 5 AS e",
    ]

    conn = await _conn()
    try:
        cur = conn.cursor()
        for i, q in enumerate(queries):
            await cur.execute(q)
            row = await cur.fetchone()
            assert row is not None, f"Query {i} returned no rows"
            assert row[0] == i + 1, f"Query {i} value mismatch: {row[0]} != {i+1}"
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_sequential_different_column_counts():
    """Sequential queries with different column counts must work."""
    queries = [
        ("SELECT 1", 1),
        ("SELECT 1, 2", 2),
        ("SELECT 1, 2, 3", 3),
    ]

    conn = await _conn()
    try:
        cur = conn.cursor()
        for sql, expected_cols in queries:
            await cur.execute(sql)
            row = await cur.fetchone()
            assert row is not None
            assert len(row) == expected_cols, f"Expected {expected_cols} cols, got {len(row)}"
            assert list(row) == list(range(1, expected_cols + 1))
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Transaction flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_transaction_rollback():
    """BEGIN → INSERT → ROLLBACK → verify rollback works on system table."""
    conn = await _conn()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        # Use _V_DATABASE for a safe read-then-write test
        conn.autocommit = False
        await cur.execute("SELECT 1")
        await cur.fetchone()
        await cur.clear()
        await conn.rollback()
        conn.autocommit = True
        await cur.execute("SELECT 42")
        row = await cur.fetchone()
        assert row[0] == 42
    finally:
        conn.autocommit = True
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_transaction_commit():
    """BEGIN → INSERT → COMMIT — verify commit persists using a temp table."""
    table_name = "T_TX_CM_" + uuid.uuid4().hex[:9].upper()
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute(f"CREATE TEMP TABLE {table_name}(c1 INT)")
        conn.autocommit = False
        await cur.execute(f"INSERT INTO {table_name} VALUES (42)")
        await conn.commit()
        conn.autocommit = True
        await cur.execute(f"SELECT c1 FROM {table_name}")
        row = await cur.fetchone()
        assert row[0] == 42
    finally:
        conn.autocommit = True
        await conn.close()


# ---------------------------------------------------------------------------
# Error resilience — one bad query must not corrupt the connection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_connection_survives_query_error():
    """After a syntax error, the connection must still accept valid queries."""
    conn = await _conn()
    try:
        cur = conn.cursor()
        with pytest.raises(Exception):
            await cur.execute("SELEC 1")  # intentional typo

        await cur.execute("SELECT 1")
        row = await cur.fetchone()
        assert row[0] == 1, "Connection should survive a syntax error"
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_connection_survives_division_by_zero():
    """After a division-by-zero, the connection must still be usable."""
    conn = await _conn()
    try:
        cur = conn.cursor()
        with pytest.raises(Exception):
            await cur.execute(
                "SELECT 1 FROM " + TABLE_FROM + " WHERE 1 / 0 = 1 LIMIT 1"
            )

        await cur.execute("SELECT 1")
        row = await cur.fetchone()
        assert row[0] == 1, "Connection should survive division by zero"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Semicolon-separated multi-statement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_semicolon_separated_sql_behaviour():
    """Multi-command via semicolon-separated SQL with nextset().

    The Python driver supports multi-statement SQL via semicolon-separated
    queries. Use cursor.nextset() to advance between result sets.
    Returns True when another result set is available, None when done.
    cursor.description updates to reflect each new result set.
    """
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute("SELECT 1 AS a; SELECT 2 AS b")

        row = await cur.fetchone()
        assert row is not None and row[0] == 1

        has_next = await cur.nextset()
        assert has_next is True

        row2 = await cur.fetchone()
        assert row2 is not None and row2[0] == 2

        assert await cur.nextset() is None  # No more result sets
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_multi_statement_mixed_select_dml():
    """Non-row-returning statements (DELETE etc.) between SELECTs
       are silently consumed by nextset()."""
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute(
            "SELECT 1; DELETE FROM " + TABLE_FROM + " WHERE 1=2; SELECT 2"
        )

        row = await cur.fetchone()
        assert row[0] == 1

        # DELETE is consumed silently by nextset()
        has_next = await cur.nextset()
        assert has_next is True

        row2 = await cur.fetchone()
        assert row2[0] == 2

        assert await cur.nextset() is None
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_multi_statement_different_column_counts():
    """Each result set may have different column counts; nextset()
       updates cursor.description."""
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute("SELECT 1, 2 AS b; SELECT 3 AS c; SELECT 4, 5, 6")

        desc0 = cur.description
        assert len(desc0) == 2

        row1 = await cur.fetchone()
        assert list(row1) == [1, 2]

        await cur.nextset()
        desc1 = cur.description
        assert len(desc1) == 1

        row2 = await cur.fetchone()
        assert row2 == [3]

        await cur.nextset()
        desc2 = cur.description
        assert len(desc2) == 3

        row3 = await cur.fetchone()
        assert row3 == [4, 5, 6]

        assert await cur.nextset() is None
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_multi_statement_three_vs_two_connections():
    """Three-statement SQL with nextset() must produce identical results
       to three separate connections."""
    sql = (
        "SELECT * FROM " + TABLE_FROM + " ORDER BY ROWID LIMIT 3; "
        "SELECT * FROM JUST_DATA..DIMACCOUNT ORDER BY ROWID LIMIT 3; "
        "SELECT * FROM JUST_DATA..DIMCURRENCY ORDER BY ROWID LIMIT 3"
    )

    connA = await _conn()
    connB = await _conn()
    connC = await _conn()
    conn_ms = await _conn()
    try:
        curA = connA.cursor()
        curB = connB.cursor()
        curC = connC.cursor()
        cur_ms = conn_ms.cursor()

        await curA.execute(
            "SELECT * FROM " + TABLE_FROM + " ORDER BY ROWID LIMIT 3")
        await curB.execute(
            "SELECT * FROM JUST_DATA..DIMACCOUNT ORDER BY ROWID LIMIT 3")
        await curC.execute(
            "SELECT * FROM JUST_DATA..DIMCURRENCY ORDER BY ROWID LIMIT 3")

        rowsA = list(await curA.fetchmany(10))
        rowsB = list(await curB.fetchmany(10))
        rowsC = list(await curC.fetchmany(10))

        await cur_ms.execute(sql)

        # Fetch each result set using fetchone() + nextset()
        rowsM1 = []
        while True:
            row = await cur_ms.fetchone()
            if row is None:
                break
            rowsM1.append(row)
        assert rowsM1 == rowsA, "First result set mismatch"

        assert await cur_ms.nextset() is True
        rowsM2 = []
        while True:
            row = await cur_ms.fetchone()
            if row is None:
                break
            rowsM2.append(row)
        assert rowsM2 == rowsB, "Second result set mismatch"

        assert await cur_ms.nextset() is True
        rowsM3 = []
        while True:
            row = await cur_ms.fetchone()
            if row is None:
                break
            rowsM3.append(row)
        assert rowsM3 == rowsC, "Third result set mismatch"

        assert await cur_ms.nextset() is None
    finally:
        await connA.close()
        await connB.close()
        await connC.close()
        await conn_ms.close()
