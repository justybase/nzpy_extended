"""Live checks for the Phase 1 fixes. Run only against a disposable database.

Every object is named ZZ_PHASE1_<uuid> and dropped in a finally block.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

import pytest

import nzpy_extended as nz

pytestmark = pytest.mark.full


def _name() -> str:
    return "ZZ_PHASE1_" + uuid.uuid4().hex[:12].upper()


async def _drop(con: Any, table: str) -> None:
    try:
        cur = con.cursor()
        await cur.execute(f"DROP TABLE {table}")
    except Exception:
        pass


@pytest.mark.asyncio
async def test_nan_and_infinity_parameters_round_trip(con: Any) -> None:
    cur = con.cursor()
    await cur.execute("select ? as a, ? as b", (float("nan"), float("inf")))
    ((a, b),) = await cur.fetchall()
    assert math.isnan(a)
    assert b == float("inf")


@pytest.mark.asyncio
async def test_negative_infinity_parameter_keeps_its_sign_on_the_server(con: Any) -> None:
    # Checked on the server, not through the decoder: the value read back is +inf
    # (see docs), so the sign has to be verified inside the SQL.
    cur = con.cursor()
    await cur.execute("select cast(? as double precision) < 0", (float("-inf"),))
    ((negative,),) = await cur.fetchall()
    assert negative is True


@pytest.mark.asyncio
async def test_nan_cannot_be_stored_and_the_server_says_so(con: Any) -> None:
    # Netezza evaluates NaN in a SELECT list but refuses to store it:
    # "Float8 result is a NaN". The driver must surface that server error,
    # not a client-side 'Attribute NAN not found'.
    table = _name()
    cur = con.cursor()
    await cur.execute(f"CREATE TABLE {table} (v DOUBLE PRECISION)")
    try:
        with pytest.raises(nz.Error, match="NaN"):
            await cur.execute(f"INSERT INTO {table} VALUES (?)", (float("nan"),))
    finally:
        await _drop(con, table)


@pytest.mark.asyncio
async def test_negative_infinity_round_trips_through_a_table_column(con: Any) -> None:
    # Stored in a column, -inf reads back as -inf. Only a bare SELECT-list
    # expression comes back as +inf (server behaviour; see the docs).
    table = _name()
    cur = con.cursor()
    await cur.execute(f"CREATE TABLE {table} (v DOUBLE PRECISION)")
    try:
        await cur.execute(f"INSERT INTO {table} VALUES (?)", (float("-inf"),))
        await cur.execute(f"SELECT v FROM {table}")
        ((v,),) = await cur.fetchall()
        assert v == float("-inf")
    finally:
        await _drop(con, table)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1e20, 1e-5, 1.5])
async def test_finite_float_literals_are_accepted_by_the_server(con: Any, value: float) -> None:
    cur = con.cursor()
    await cur.execute("select cast(? as double precision) as v", (value,))
    ((v,),) = await cur.fetchall()
    assert v == value


@pytest.mark.asyncio
async def test_backslash_in_a_literal_does_not_hide_a_following_parameter(con: Any) -> None:
    # Netezza has no E'' strings: 'x\\' is a complete literal (two backslashes).
    cur = con.cursor()
    await cur.execute("select 'x\\\\' as s, ? as p", ("Y",))
    ((s, p),) = await cur.fetchall()
    assert s == "x\\\\"
    assert p == "Y"


@pytest.mark.asyncio
async def test_executemany_raises_the_same_error_class_as_execute(con: Any) -> None:
    table = _name()
    cur = con.cursor()
    await cur.execute(f"CREATE TABLE {table} (id INT)")
    try:
        with pytest.raises(nz.Error) as single:
            await cur.execute(f"INSERT INTO {table} VALUES (?)", ("not_an_int",))
        with pytest.raises(nz.Error) as many:
            await cur.executemany(
                f"INSERT INTO {table} VALUES (?)", [(1,), ("not_an_int",), (3,)]
            )
        assert type(many.value) is type(single.value)
        assert many.value.__notes__ == ["executemany failed at param set 2/3"]
    finally:
        await _drop(con, table)
