"""
Transaction tests — C# TransactionTests / Node TransactionTests parity.
"""

import os
import uuid

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
async def test_transaction_rollback_drops_table():
    """Mirrors Node Basic Transaction Rollback / C# T2 rollback."""
    table = "T_TX_RB_" + uuid.uuid4().hex[:8].upper()
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        await cur.execute(f"DROP TABLE {table} IF EXISTS")

        conn.autocommit = False
        await cur.execute(
            f"CREATE TABLE {table}(c1 numeric(10,5), c2 varchar(10), c3 nchar(5))"
        )
        await cur.execute(
            f"INSERT INTO {table} VALUES (123.54, 'xcfd', 'xyz')"
        )
        await conn.rollback()

        conn.autocommit = True
        with pytest.raises(nzpy.Error):
            await cur.execute(f"SELECT * FROM {table}")
            await cur.fetchall()
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_transaction_commit_persists_table():
    """Mirrors Node Basic Transaction Commit / C# T5 commit."""
    table = "T_TX_CM_" + uuid.uuid4().hex[:8].upper()
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        await cur.execute(f"DROP TABLE {table} IF EXISTS")

        conn.autocommit = False
        await cur.execute(
            f"CREATE TABLE {table}(c1 numeric(10,5), c2 varchar(10), c3 nchar(5))"
        )
        await cur.execute(
            f"INSERT INTO {table} VALUES (123.54, 'xcfd', 'xyz')"
        )
        await conn.commit()

        conn.autocommit = True
        await cur.execute(f"SELECT * FROM {table}")
        rows = await cur.fetchall()
        assert len(rows) == 1

        await cur.execute(f"DROP TABLE {table} IF EXISTS")
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_autocommit_false_insert_rollback():
    """Insert rolled back when autocommit is off."""
    table = "T_TX_AC_" + uuid.uuid4().hex[:8].upper()
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        await cur.execute(f"DROP TABLE {table} IF EXISTS")
        await cur.execute(f"CREATE TABLE {table}(c1 INT) DISTRIBUTE ON RANDOM")

        conn.autocommit = False
        await cur.execute(f"INSERT INTO {table} VALUES (1)")
        await conn.rollback()

        conn.autocommit = True
        await cur.execute(f"SELECT COUNT(*) FROM {table}")
        assert int((await cur.fetchone())[0]) == 0

        await cur.execute(f"DROP TABLE {table} IF EXISTS")
    finally:
        await conn.close()
