"""Live regression tests for the 0.9 driver-semantics fixes.

Every scenario here was reproduced on a real appliance before the fix: wrong
exception classes, silently ignored statements in an aborted transaction, an
open transaction handed to the next pool borrower, rows silently dropped from a
cursor, VARBINARY decoded as text, and sessions lost to client-side mistakes.
Only TEMP tables are created.
"""

from __future__ import annotations

import pytest

import nzpy_extended as nzpy
from nzpy_extended.exceptions import (
    DataError,
    IntegrityError,
    InterfaceError,
    InternalError,
    OperationalError,
    ProgrammingError,
)

pytestmark = pytest.mark.full


async def _rows(conn: nzpy.Connection, sql: str, args: object = None) -> list:
    cur = conn.cursor()
    await cur.execute(sql, args)
    return await cur.fetchall()


@pytest.mark.asyncio
@pytest.mark.parametrize("sql, expected", [
    ("SELECT 1/0", DataError),
    ("SELECT CAST('abc' AS INT)", DataError),
    ("SELECT CAST('2020-13-45' AS DATE)", DataError),
    ("SELECT CAST(123456.789 AS NUMERIC(4,2))", DataError),
    ("SELECT * FROM ZZ_RUST_NO_SUCH_TABLE_09", ProgrammingError),
    ("SELEC 1", ProgrammingError),
])
async def test_server_errors_have_their_db_api_class(con, sql, expected) -> None:
    with pytest.raises(expected) as info:
        await _rows(con, sql)
    assert type(info.value) is expected
    message = str(info.value)
    assert not message.startswith("{"), message
    assert "ERROR:" not in message


@pytest.mark.asyncio
async def test_not_null_violation_is_an_integrity_error(con) -> None:
    await _rows(con, "CREATE TEMP TABLE ZZ_RUST_SEM_NN (n INT NOT NULL)")
    with pytest.raises(IntegrityError, match="null"):
        await _rows(con, "INSERT INTO ZZ_RUST_SEM_NN VALUES (NULL)")


@pytest.mark.asyncio
async def test_error_of_a_later_statement_keeps_its_class(con) -> None:
    cur = con.cursor()
    await cur.execute("SELECT 1; SELECT 1/0")
    with pytest.raises(DataError) as info:
        await cur.fetchall()
        await cur.nextset()
        await cur.fetchall()
    assert type(info.value) is DataError


@pytest.mark.asyncio
async def test_aborted_transaction_raises_and_commit_reports_the_rollback(con) -> None:
    await _rows(con, "CREATE TEMP TABLE ZZ_RUST_SEM_TX (v INT)")
    await _rows(con, "BEGIN")
    assert con.in_transaction
    await _rows(con, "INSERT INTO ZZ_RUST_SEM_TX VALUES (1)")
    with pytest.raises(DataError):
        await _rows(con, "SELECT 1/0")
    with pytest.raises(InternalError, match="aborted"):
        await _rows(con, "SELECT COUNT(*) FROM ZZ_RUST_SEM_TX")
    with pytest.raises(InternalError, match="no changes were saved"):
        await con.commit()
    assert not con.in_transaction
    assert await _rows(con, "SELECT COUNT(*) FROM ZZ_RUST_SEM_TX") == [[0]]


@pytest.mark.asyncio
async def test_explicit_begin_is_tracked_and_rolled_back(con) -> None:
    await _rows(con, "CREATE TEMP TABLE ZZ_RUST_SEM_RB (v INT)")
    await _rows(con, "BEGIN")
    await _rows(con, "INSERT INTO ZZ_RUST_SEM_RB VALUES (1)")
    assert con.in_transaction
    await con.rollback()
    assert not con.in_transaction
    assert await _rows(con, "SELECT COUNT(*) FROM ZZ_RUST_SEM_RB") == [[0]]


@pytest.mark.asyncio
async def test_pool_does_not_hand_an_open_transaction_to_the_next_borrower(db_kwargs_fn) -> None:
    pool = nzpy.NzPool(min_size=0, max_size=1, **db_kwargs_fn)
    try:
        conn = await pool.acquire()
        await _rows(conn, "CREATE TEMP TABLE ZZ_RUST_SEM_POOL (v INT)")
        await _rows(conn, "BEGIN")
        await _rows(conn, "INSERT INTO ZZ_RUST_SEM_POOL VALUES (1)")
        await pool.release(conn)
        again = await pool.acquire()
        assert again is conn
        assert not again.in_transaction
        assert await _rows(again, "SELECT COUNT(*) FROM ZZ_RUST_SEM_POOL") == [[0]]
        await pool.release(again)
    finally:
        await pool.close_all()


@pytest.mark.asyncio
async def test_discarded_cursor_raises_instead_of_returning_nothing(con) -> None:
    first, second = con.cursor(), con.cursor()
    await first.execute("SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3")
    assert await first.fetchone() is not None
    await second.execute("SELECT 42")
    assert await second.fetchall() == [[42]]
    with pytest.raises(InterfaceError, match="discarded"):
        await first.fetchall()


@pytest.mark.asyncio
async def test_varbinary_and_geometry_are_bytes(con) -> None:
    await _rows(con, "CREATE TEMP TABLE ZZ_RUST_SEM_BIN (b VARBINARY(16), g ST_GEOMETRY(100))")
    await _rows(con, "INSERT INTO ZZ_RUST_SEM_BIN VALUES (x'00ff80fe41', x'0101000000')")
    assert await _rows(con, "SELECT b, g FROM ZZ_RUST_SEM_BIN") == [
        [b"\x00\xff\x80\xfeA", b"\x01\x01\x00\x00\x00"]]


@pytest.mark.asyncio
async def test_bytes_parameter_round_trips_through_varbinary(con) -> None:
    await _rows(con, "CREATE TEMP TABLE ZZ_RUST_SEM_BP (b VARBINARY(16))")
    await _rows(con, "INSERT INTO ZZ_RUST_SEM_BP VALUES (?)", (b"\x00\xff\x10",))
    assert await _rows(con, "SELECT b FROM ZZ_RUST_SEM_BP") == [[b"\x00\xff\x10"]]


@pytest.mark.asyncio
async def test_client_side_mistakes_do_not_cost_the_session(con) -> None:
    with pytest.raises(ProgrammingError):
        await _rows(con, "SELECT ?, ?", (1,))
    with pytest.raises(ProgrammingError):
        await _rows(con, "SELECT ?", (1, 2))
    with pytest.raises(DataError):
        await _rows(con, "SELECT ?", ("a\x00b",))
    assert await _rows(con, "SELECT 7") == [[7]]


@pytest.mark.asyncio
async def test_metadata_filters_are_not_injectable(con) -> None:
    hostile = "NO_SUCH_SCHEMA_X' OR '1'='1"
    assert await con.meta.get_tables(schema=hostile) == []
    assert await con.meta.get_views(schema=hostile) == []


@pytest.mark.asyncio
async def test_cancelled_query_is_an_operational_error(con) -> None:
    import asyncio

    async def cancel_soon() -> None:
        await asyncio.sleep(1.0)
        await con.cancel()

    task = asyncio.ensure_future(cancel_soon())
    try:
        with pytest.raises(OperationalError, match="cancel"):
            await _rows(con, "SELECT COUNT(*) FROM _v_relation_column a, "
                             "_v_relation_column b, _v_relation_column c")
    finally:
        await task
