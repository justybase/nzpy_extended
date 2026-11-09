import asyncio
import os
import uuid

import pytest

import nzpy_extended as nzpy
from nzpy_extended.pool import NzPool

pytestmark = pytest.mark.smoke

NZ_HOST     = os.environ.get("NZ_DEV_HOST",     "127.0.0.1")
NZ_PORT     = int(os.environ.get("NZ_DEV_PORT",  "5480"))
NZ_DB       = os.environ.get("NZ_DEV_DB",        "JUST_DATA")
NZ_USER     = os.environ.get("NZ_DEV_USER",      "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD",  "password")

_HEAVY_SQL_TIMEOUT = """
    SELECT F1.PRODUCTKEY, COUNT(DISTINCT (F1.PRODUCTKEY / F2.PRODUCTKEY))
    FROM
    ( SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY LIMIT 30000) F1,
    ( SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY LIMIT 30000) F2
    GROUP BY 1
    LIMIT 500
"""


async def _conn():
    return await nzpy.connect(
        user=NZ_USER, password=NZ_PASSWORD,
        host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
    )


@pytest.mark.asyncio
async def test_connection():
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute("SELECT 1")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == 1
    finally:
        await conn.close()


@pytest.mark.parametrize("sql,expected", [
    ("SELECT 12345::BIGINT",                        12345),
    ("SELECT 3.14::FLOAT",                          3.14),
    ("SELECT 3.14::DOUBLE PRECISION",               3.14),
    ("SELECT 15::BYTEINT",                          15),
    ("SELECT 25000::SMALLINT",                      25000),
    ("SELECT 12345678::INTEGER",                    12345678),
    ("SELECT 'abc'::VARCHAR(10)",                   "abc"),
    ("SELECT 'Hello'::NCHAR(10)",                   "Hello"),
    ("SELECT 'World'::NVARCHAR(10)",                "World"),
    ("SELECT true::BOOLEAN",                        True),
    ("SELECT NULL",                                 None),
])
@pytest.mark.asyncio
async def test_basic_types(sql, expected):
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        row = await cur.fetchone()
        assert row is not None
        val = row[0]
        if isinstance(val, str) and isinstance(expected, str):
            assert val.strip() == expected
        else:
            assert val == expected or str(val) == str(expected)
    finally:
        await conn.close()


@pytest.mark.parametrize("sql", [
    "SELECT '2024-01-01'::DATE",
    "SELECT '12:00:00'::TIME",
    "SELECT '2024-12-11 14:30:00'::TIMESTAMP",
    "SELECT NOW()",
    "SELECT 123.456::NUMERIC(10,3)",
    "SELECT 9223372036854775807::BIGINT",
    "SELECT -128::BYTEINT",
])
@pytest.mark.asyncio
async def test_extra_types_return_data(sql):
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is not None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_null_handling():
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute("SELECT NULL::VARCHAR(10), 'abc', NULL::INTEGER")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is None
        assert row[1] == "abc"
        assert row[2] is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_error_handling():
    conn = await _conn()
    try:
        cur = conn.cursor()
        with pytest.raises(nzpy.Error):
            await cur.execute("SELECT 1/0")
            await cur.fetchall()
        cur2 = conn.cursor()
        await cur2.execute("SELECT CURRENT_CATALOG")
        row = await cur2.fetchone()
        assert row is not None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_invalid_sql():
    conn = await _conn()
    try:
        cur = conn.cursor()
        with pytest.raises(nzpy.Error):
            await cur.execute("SELECT invalid_column FROM non_existent_table")
            await cur.fetchall()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_version():
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute("SELECT VERSION()")
        row = await cur.fetchone()
        assert row is not None
        assert len(str(row[0])) > 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_multi_row():
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute("SELECT * FROM JUST_DATA.ADMIN.DIMDATE ORDER BY ROWID LIMIT 10")
        rows = await cur.fetchall()
        assert len(rows) == 10
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_expression():
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute("SELECT 1+1, 2*3, 10/2, 10-3")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == 2
        assert row[1] == 6
        assert row[2] == 5
        assert row[3] == 7
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_conditional():
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute("SELECT CASE WHEN 1=1 THEN 'yes' ELSE 'no' END")
        row = await cur.fetchone()
        assert row is not None
        assert row[0].strip() == "yes"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_command_timeout():
    conn = await _conn()
    try:
        cur = conn.cursor()
        with pytest.raises(nzpy.OperationalError):
            await cur.execute(_HEAVY_SQL_TIMEOUT, timeout=2.0)
            await cur.fetchall()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_manual_cancel():
    conn = await _conn()
    try:
        cur = conn.cursor()

        async def run_long():
            try:
                await cur.execute(_HEAVY_SQL_TIMEOUT)
                await cur.fetchall()
            except Exception:
                pass

        task = asyncio.create_task(run_long())
        await asyncio.sleep(0.5)
        await conn.cancel()
        await task
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_transaction_rollback():
    conn = await _conn()
    table = "TMP_SMOKE_PY_" + uuid.uuid4().hex[:9].upper()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        await cur.execute(f"CREATE TEMP TABLE {table} (c1 INT)")

        conn.autocommit = False
        await cur.execute(f"INSERT INTO {table} VALUES (1)")
        await conn.rollback()

        conn.autocommit = True
        await cur.execute(f"SELECT COUNT(*) FROM {table}")
        row = await cur.fetchone()
        assert int(row[0]) == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_transaction_commit():
    conn = await _conn()
    table = "TMP_SMOKE_PY_" + uuid.uuid4().hex[:9].upper()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        await cur.execute(f"CREATE TEMP TABLE {table} (c1 INT)")

        conn.autocommit = False
        await cur.execute(f"INSERT INTO {table} VALUES (1)")
        await conn.commit()

        conn.autocommit = True
        await cur.execute(f"SELECT COUNT(*) FROM {table}")
        row = await cur.fetchone()
        assert int(row[0]) == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_multi_statement():
    conn = await _conn()
    try:
        cur = conn.cursor()
        await cur.execute("SELECT 1 AS a; SELECT 2 AS b")
        rows = await cur.fetchall()
        assert rows[0][0] == 1

        moved = await cur.nextset()
        assert moved is True
        rows = await cur.fetchall()
        assert rows[0][0] == 2

        moved = await cur.nextset()
        assert moved is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_pool_basic():
    pool = NzPool(max_size=2, min_size=0,
                  user=NZ_USER, password=NZ_PASSWORD,
                  host=NZ_HOST, port=NZ_PORT, database=NZ_DB)
    try:
        conn = await pool.acquire()
        assert conn is not None
        cur = conn.cursor()
        await cur.execute("SELECT 1")
        row = await cur.fetchone()
        assert row[0] == 1
        await pool.release(conn)
    finally:
        await pool.close_all()
