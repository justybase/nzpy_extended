import os

import pytest
import nzpy_extended as nzpy

from reference_driver import ReferenceConnection, compare_reference_rows

pytestmark = pytest.mark.smoke

NZ_HOST     = os.environ.get("NZ_DEV_HOST",     "192.168.0.144")
NZ_PORT     = int(os.environ.get("NZ_DEV_PORT",  "5480"))
NZ_DB       = os.environ.get("NZ_DEV_DATABASE") or os.environ.get("NZ_DEV_DB", "JUST_DATA")
NZ_USER     = os.environ.get("NZ_DEV_USER",      "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD",  "password")


SMOKE_QUERIES = [
    "SELECT 1",
    "SELECT 12345::BIGINT",
    "SELECT 3.14::FLOAT",
    "SELECT 3.14::DOUBLE PRECISION",
    "SELECT 123.456::NUMERIC(10,3)",
    "SELECT CAST('2023-01-01'::DATE AS VARCHAR(50))",
    "SELECT CAST('12:00:00'::TIME AS VARCHAR(50))",
    "SELECT '2024-12-11 14:30:00'::TIMESTAMP",
    "SELECT 'abc'::VARCHAR(10)",
    "SELECT 'abc'::NCHAR(10)",
    "SELECT 'abc'::NVARCHAR(10)",
    "SELECT 15::BYTEINT",
    "SELECT 25000::SMALLINT",
    "SELECT true::BOOLEAN",
    "SELECT NULL",
    "SELECT * FROM JUST_DATA.ADMIN.DIMDATE ORDER BY ROWID LIMIT 5",
    "SELECT * FROM JUST_DATA.ADMIN.DIMACCOUNT ORDER BY ROWID LIMIT 5",
]


async def _nzpy_conn():
    try:
        return await nzpy.connect(
            user=NZ_USER, password=NZ_PASSWORD,
            host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
        )
    except Exception as e:
        pytest.skip(f"nzpy connection failed: {e}")


@pytest.mark.parametrize("sql", SMOKE_QUERIES)
@pytest.mark.asyncio
async def test_query_matches_reference_driver(sql):
    reference_con = ReferenceConnection()
    nzpy_con = await _nzpy_conn()
    try:
        nz_cur = nzpy_con.cursor()
        reference_cur = reference_con.cursor()
        try:
            await nz_cur.execute(sql)
            nz_rows = await nz_cur.fetchall()

            reference_cur.execute(sql)
            reference_rows = reference_cur.fetchall()

            compare_reference_rows(
                nz_rows, reference_rows, sql, reference_cur.description
            )
        finally:
            await nz_cur.close()
            reference_cur.close()
    finally:
        reference_con.close()
        await nzpy_con.close()
