"""
test_has_rows.py
================
Python equivalent of HasRowsTests.cs from JustyBase.NetezzaDriver.Tests.

The nzpy_extended cursor does not expose a HasRows property directly, but we can
test the equivalent semantics:
  - A SELECT that returns 0 rows vs ≥ 1 rows.
  - Multiple result sets via multi-statement SQL.
  - DML statements (DELETE … WHERE 1=2) affect row availability.

Note: nzpy_extended uses ``asyncio_mode = auto``; multi-result handling is tested
via multiple executes or explicit UNION queries.
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


async def _make_conn():
    return await nzpy.connect(**CONN_KWARGS)


# ---------------------------------------------------------------------------
# has_rows semantics – LIMIT 0 vs LIMIT 1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_limit_zero_returns_no_rows():
    """SELECT with LIMIT 0 must return an empty result set."""
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        await cur.execute(
            "SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY ORDER BY ROWID LIMIT 0"
        )
        rows = await cur.fetchall()
        assert rows == [], f"Expected empty list, got {rows}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_limit_one_has_rows():
    """SELECT with LIMIT 1 must return exactly one row."""
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        await cur.execute(
            "SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY ORDER BY ROWID LIMIT 1"
        )
        rows = await cur.fetchall()
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Multiple result sets simulation — via sequential queries on same connection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sequential_selects_match_expected():
    """
    Mirrors ManyResults: execute multiple independent selects and verify
    results are independent of each other.
    """
    conn = await _make_conn()
    try:
        cur = conn.cursor()

        await cur.execute(
            "SELECT 1 FROM JUST_DATA..DIMDATE LIMIT 1"
        )
        r1 = await cur.fetchall()
        assert len(r1) == 1 and int(r1[0][0]) == 1

        await cur.execute(
            "SELECT 2 FROM JUST_DATA..DIMDATE LIMIT 1"
        )
        r2 = await cur.fetchall()
        assert len(r2) == 1 and int(r2[0][0]) == 2

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_delete_no_matching_rows_returns_empty():
    """
    Mirrors Test1: DELETE WHERE 1=2 affects 0 rows and the next SELECT
    works normally.
    """
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        # Netezza: DELETE WHERE 1=2 – no rows affected, no error
        await cur.execute("DELETE FROM JUST_DATA..FACTPRODUCTINVENTORY WHERE 1=2")

        # Follow-up SELECT must still work
        await cur.execute(
            "SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY ORDER BY ROWID LIMIT 1"
        )
        row = await cur.fetchone()
        assert row is not None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_has_rows_multi_statement_nextset():
    """Mirrors C# HasRowsTests Helper — has_rows per result set via nextset()."""
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        sql = (
            "SELECT 1 FROM JUST_DATA..DIMDATE LIMIT 1; "
            "SELECT 2 FROM JUST_DATA..DIMDATE LIMIT 1"
        )
        await cur.execute(sql)
        expected = [True, True]
        for i, exp in enumerate(expected):
            assert cur.has_rows == exp, (
                f"result set {i}: expected has_rows={exp}, got {cur.has_rows}"
            )
            rows = []
            while True:
                row = await cur.fetchone()
                if row is None:
                    break
                rows.append(row)
            assert (len(rows) > 0) == exp, (
                f"result set {i}: expected rows={exp}, got {len(rows)}"
            )
            if i < len(expected) - 1:
                assert await cur.nextset() is True
        assert await cur.nextset() is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_alternating_empty_and_nonempty_selects():
    """
    Mirrors Test2: a pattern of LIMIT 0 / LIMIT 1 / LIMIT 0 / LIMIT 1.
    Each query is executed independently; result sets are [[], [row], [], [row]].
    """
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        base = "SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY ORDER BY ROWID"

        expected_has_rows = [False, True, False, True]
        sqls = [
            f"{base} LIMIT 0",
            f"{base} LIMIT 1",
            f"{base} LIMIT 0",
            f"{base} LIMIT 1",
        ]
        for sql, expected in zip(sqls, expected_has_rows):
            await cur.execute(sql)
            rows = await cur.fetchall()
            has_rows = len(rows) > 0
            assert has_rows == expected, (
                f"SQL: {sql!r} — expected has_rows={expected}, got {has_rows} "
                f"(rows={rows!r})"
            )

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_dml_and_select_sequence():
    """
    Mirrors Test3/Test4: mix of DELETE (no rows) and SELECT.
    Verifies that the driver correctly handles the mixed sequence.
    """
    conn = await _make_conn()
    try:
        cur = conn.cursor()

        # two no-op deletes
        await cur.execute("DELETE FROM JUST_DATA..FACTPRODUCTINVENTORY WHERE 1=2")
        await cur.execute("DELETE FROM JUST_DATA..FACTPRODUCTINVENTORY WHERE 1=2")

        # final SELECT must return rows
        await cur.execute(
            "SELECT 11 FROM JUST_DATA..FACTPRODUCTINVENTORY ORDER BY ROWID LIMIT 10"
        )
        rows = await cur.fetchall()
        assert len(rows) == 10
        assert all(int(r[0]) == 11 for r in rows)

    finally:
        await conn.close()
