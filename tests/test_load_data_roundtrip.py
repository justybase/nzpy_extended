"""Live load_data round trips: every shared fixture is loaded, read back and compared.

Each test uses its own ``ZZ_LDX_*`` table, dropped in ``finally``; the catalog
(``_v_table``) is checked afterwards so a failing test cannot leave objects behind.
Run against a disposable database only.
"""

from __future__ import annotations

import re
import uuid

import pytest

import nzpy_extended as nzpy
from tests.load_data_fixtures import ERROR_CASES, ROUND_TRIPS

pytestmark = pytest.mark.full


def _table_name() -> str:
    return "ZZ_LDX_" + uuid.uuid4().hex[:12].upper()


def _sorted(rows) -> list[tuple]:
    return sorted((tuple(r) for r in rows), key=repr)


async def _table_exists(conn, table: str) -> bool:
    cur = conn.cursor()
    await cur.execute(f"SELECT COUNT(*) FROM _v_table WHERE tablename = '{table}'")
    return (await cur.fetchone())[0] > 0


@pytest.mark.parametrize("case", ROUND_TRIPS, ids=lambda c: c.name)
async def test_round_trip(con, case):
    table = _table_name()
    cur = con.cursor()
    try:
        if case.existing_ddl:
            await cur.execute(f"CREATE TABLE {table} ({case.existing_ddl})")
        expected = case.expected()
        count = await con.load_data(table, case.rows(), columns=case.columns, **case.kwargs)
        assert count == len(expected)

        await cur.execute(f"SELECT * FROM {table}")
        assert _sorted(await cur.fetchall()) == _sorted(expected)
    finally:
        await cur.execute(f"DROP TABLE {table} IF EXISTS")
        assert not await _table_exists(con, table)


@pytest.mark.parametrize("case", ERROR_CASES, ids=lambda c: c.name)
async def test_bad_input_is_refused_and_leaves_no_table(con, case):
    table = case.table if case.table is not None else _table_name()
    cur = con.cursor()
    created = False
    try:
        if case.existing_ddl:
            await cur.execute(f"CREATE TABLE {table} ({case.existing_ddl})")
            created = True
        with pytest.raises(case.exc, match=re.escape(case.fragment)):
            await con.load_data(table, case.rows(), columns=case.columns, **case.kwargs)

        if case.existing_ddl:
            await cur.execute(f"SELECT COUNT(*) FROM {table}")
            assert (await cur.fetchone())[0] == 0
        elif case.table is None:
            assert not await _table_exists(con, table)
    finally:
        if created:
            await cur.execute(f"DROP TABLE {table} IF EXISTS")
            assert not await _table_exists(con, table)


async def _load_then_fail_mid_stream(db_kwargs, table, rows, columns):
    """Load a generator that fails part-way; return the exception and whether the table survived."""
    conn = await nzpy.connect(**db_kwargs)
    try:
        with pytest.raises(Exception) as info:
            await conn.load_data(table, rows(), columns=columns)
    finally:
        # The server ends the session after an aborted stream, so close may already have happened.
        try:
            await conn.close()
        except Exception:
            pass

    check = await nzpy.connect(**db_kwargs)
    try:
        survived = await _table_exists(check, table)
    finally:
        await check.close()
    return info.value, survived


async def test_client_error_mid_stream_leaves_no_table(db_kwargs_fn):
    def rows():
        for i in range(3):
            yield (i, f"v{i}")
        raise RuntimeError("client boom mid-stream")

    exc, survived = await _load_then_fail_mid_stream(
        db_kwargs_fn, _table_name(), rows, [("id", "INT"), ("s", "VARCHAR(20)")])
    assert isinstance(exc, RuntimeError)
    assert "client boom" in str(exc)
    assert not survived


async def test_bad_value_mid_stream_names_row_and_leaves_no_table(db_kwargs_fn):
    def rows():
        for i in range(2000):
            yield (i, f"v{i}")
        yield ("not-an-int", "bad")

    exc, survived = await _load_then_fail_mid_stream(
        db_kwargs_fn, _table_name(), rows, [("id", "INT"), ("s", "VARCHAR(20)")])
    assert isinstance(exc, nzpy.DataError)
    assert "row 2001, column id" in str(exc)
    assert not survived


async def test_connection_is_usable_after_validation_error(con):
    with pytest.raises(nzpy.ProgrammingError):
        await con.load_data(_table_name(), [], columns=None)
    cur = con.cursor()
    await cur.execute("SELECT 1")
    assert (await cur.fetchone())[0] == 1
