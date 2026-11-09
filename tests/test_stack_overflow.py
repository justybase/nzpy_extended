"""
Stack overflow / deep protocol loop tests — Node StackOverflowPrevention parity.
"""

import os

import pytest

import nzpy_extended as nzpy

pytestmark = pytest.mark.full

CONN_KWARGS = dict(
    user=os.environ.get("NZ_DEV_USER", "admin"),
    password=os.environ.get("NZ_DEV_PASSWORD", "password"),
    host=os.environ.get("NZ_DEV_HOST", "127.0.0.1"),
    port=int(os.environ.get("NZ_DEV_PORT", "5480")),
    database=os.environ.get("NZ_DEV_DB", "JUST_DATA"),
)


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_many_selects_multi_statement_nextset():
    """Many semicolon-separated SELECTs must not recurse-overflow the protocol loop."""
    parts = [f"SELECT {i} AS n" for i in range(200)]
    sql = "; ".join(parts)

    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        count = 0
        while True:
            while True:
                row = await cur.fetchone()
                if row is None:
                    break
                count += 1
            has_next = await cur.nextset()
            if has_next is None:
                break
        assert count == 200
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_mixed_command_complete_and_select():
    sql = (
        "SELECT 1; SELECT 2; "
        "DELETE FROM JUST_DATA..FACTPRODUCTINVENTORY WHERE 1=2; "
        "SELECT 3"
    )
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        assert cur.has_rows
        assert (await cur.fetchone())[0] == 1
        assert await cur.nextset() is True
        assert cur.has_rows
        assert (await cur.fetchone())[0] == 2
        assert await cur.nextset() is True  # DELETE + SELECT 3
        assert cur.has_rows
        assert (await cur.fetchone())[0] == 3
        assert await cur.nextset() is None
    finally:
        await conn.close()
