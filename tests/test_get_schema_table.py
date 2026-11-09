"""
test_get_schema_table.py
========================
Port of GetSchemaTableTests from Node.js and C# reference drivers.
"""

import datetime
import decimal
import os

import pytest

import nzpy_extended as nzpy

pytestmark = pytest.mark.full

NZ_HOST = os.environ.get("NZ_DEV_HOST", "127.0.0.1")
NZ_PORT = int(os.environ.get("NZ_DEV_PORT", "5480"))
NZ_DB = os.environ.get("NZ_DEV_DB", "JUST_DATA")
NZ_USER = os.environ.get("NZ_DEV_USER", "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD", "password")

CONN_KWARGS = dict(
    user=NZ_USER, password=NZ_PASSWORD,
    host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
)


def _schema_by_name(schema_rows):
    return {r['ColumnName'].upper(): r for r in schema_rows}


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_get_schema_table_returns_correct_column_schema():
    sql = """
        SELECT
            ENGLISHDAYNAMEOFWEEK,
            CAST(42 AS INTEGER) AS INT_COL,
            CAST('2024-01-01' AS DATE) AS DATE_COL,
            CAST(123.45 AS NUMERIC(10,2)) AS NUMERIC_COL,
            'text123' AS text_col3
        FROM JUST_DATA..DIMDATE D
        ORDER BY D.DATEKEY
        LIMIT 2
    """
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        schema = cur.get_schema_table()
        assert len(schema) == 5

        cols = _schema_by_name(schema)
        assert cols['ENGLISHDAYNAMEOFWEEK']['DataType'] is str
        assert cols['INT_COL']['DataType'] is int
        assert cols['DATE_COL']['DataType'] is datetime.date
        assert cols['NUMERIC_COL']['NumericPrecision'] == 10
        assert cols['NUMERIC_COL']['NumericScale'] == 2
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_get_schema_table_with_not_null_column():
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        await cur.execute("DROP TABLE TEST_NOT_NULL_PY IF EXISTS")
        await cur.execute(
            "CREATE TABLE TEST_NOT_NULL_PY (ID INT NOT NULL) DISTRIBUTE ON RANDOM"
        )
        await cur.execute("INSERT INTO TEST_NOT_NULL_PY SELECT 15")

        cur2 = conn.cursor()
        await cur2.execute("SELECT * FROM TEST_NOT_NULL_PY")
        schema = cur2.get_schema_table()
        assert schema[0]['AllowDBNull'] is False

        await cur.execute("DROP TABLE TEST_NOT_NULL_PY IF EXISTS")
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_get_schema_table_text_column_sizes():
    parts = []
    for size in range(1, 301):
        if size > 1:
            parts.append(',')
        parts.append(f"CAST('x' AS VARCHAR({size})) AS col_{size}")
    sql = "SELECT " + ''.join(parts)

    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        schema = cur.get_schema_table()
        assert len(schema) == 300

        for i, size in enumerate(range(1, 301)):
            row = schema[i]
            assert row['ColumnName'].upper() == f'COL_{size}'
            assert row['ColumnSize'] == size
            assert row['DataType'] is str
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_get_schema_table_empty_result_set():
    sql = (
        "SELECT numeric_col FROM "
        "(SELECT CAST(0 AS NUMERIC(15,5)) AS numeric_col) t WHERE 1=0"
    )
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        schema = cur.get_schema_table()
        assert len(schema) == 1
        row = schema[0]
        assert row['ColumnName'].upper() == 'NUMERIC_COL'
        assert row['NumericPrecision'] == 15
        assert row['NumericScale'] == 5
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_get_schema_table_varying_column_sizes():
    sql = """
        SELECT
            CAST('test' AS CHAR(10)) AS FIXED_CHAR,
            CAST('test' AS VARCHAR(100)) AS VAR_CHAR,
            CAST('test' AS TEXT) AS TEXT_COL
    """
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        rows = _schema_by_name(cur.get_schema_table())

        assert rows['FIXED_CHAR']['ColumnSize'] == 10
        assert rows['VAR_CHAR']['ColumnSize'] == 100
        text_size = rows['TEXT_COL']['ColumnSize']
        assert text_size == -1 or text_size >= 4
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_get_schema_table_unicode_metadata():
    sql = """
        SELECT
            'AA'::VARCHAR(32) AS VC,
            'AA'::NVARCHAR(32) AS NVC,
            'AA'::NCHAR(8) AS NC,
            'AA'::NATIONAL CHARACTER VARYING(32) AS NCV,
            CURRENT_DATE AS CD,
            CURRENT_TIMESTAMP AS CTS
        FROM JUST_DATA..DIMACCOUNT
        LIMIT 1
    """
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        schema = cur.get_schema_table()

        assert cur.get_column_metadata(0)['provider_type'] == 1043
        assert cur.get_column_metadata(1)['provider_type'] == 2530
        assert cur.get_column_metadata(2)['provider_type'] == 2522
        assert cur.get_column_metadata(4)['provider_type'] == 1082
        assert cur.get_column_metadata(5)['provider_type'] == 1184

        assert cur.get_column_metadata(0)['declared_type_name'] == 'VARCHAR(32)'
        assert cur.get_column_metadata(1)['declared_type_name'] == 'NVARCHAR(32)'
        assert cur.get_column_metadata(2)['declared_type_name'] == 'NCHAR(8)'

        assert schema[0]['ColumnSize'] == 32
        assert schema[1]['ColumnSize'] == 32
        assert schema[2]['ColumnSize'] == 8
        assert schema[0]['DataType'] is str
        assert schema[4]['DataType'] is datetime.date

        row = await cur.fetchone()
        assert row is not None
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("prec,scale", [(5, 2), (18, 6), (38, 10)])
async def test_numeric_precision_scale(prec, scale):
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute(f"SELECT 0::NUMERIC({prec},{scale}) AS COL_XYZ")
        schema = cur.get_schema_table()
        assert schema[0]['NumericPrecision'] == prec
        assert schema[0]['NumericScale'] == scale
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_get_schema_table_computed_columns():
    sql = """
        SELECT
            CAST(42 AS INTEGER) + 1 AS computed_int,
            SUBSTRING('Hello World', 1, 5) AS computed_string,
            CASE WHEN 1=1 THEN 'Y' ELSE 'N' END AS computed_case,
            COUNT(*) OVER() AS computed_window,
            123.45 * 2 AS computed_numeric
        FROM just_data..dimdate LIMIT 1
    """
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute(sql)
        rows = _schema_by_name(cur.get_schema_table())

        assert rows['COMPUTED_INT']['DataType'] is int
        assert rows['COMPUTED_STRING']['DataType'] is str
        assert rows['COMPUTED_CASE']['DataType'] is str
        assert rows['COMPUTED_NUMERIC']['DataType'] in (float, decimal.Decimal, int)
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_description_seven_tuple():
    """DB-API description exposes all seven fields when metadata is available."""
    conn = await nzpy.connect(**CONN_KWARGS)
    try:
        cur = conn.cursor()
        await cur.execute("SELECT CAST(1 AS INTEGER) AS x, CAST(3.14 AS NUMERIC(5,2)) AS n")
        desc = cur.description
        assert desc is not None
        assert len(desc) == 2
        assert len(desc[0]) == 7
        assert desc[0][0].upper() == 'X'
        assert desc[0][1] == 23  # INT4 OID
        assert desc[1][0].upper() == 'N'
        assert desc[1][1] == 1700  # NUMERIC OID
    finally:
        await conn.close()
