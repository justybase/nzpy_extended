import os
import datetime
import decimal

import pytest

import nzpy_extended as nzpy

pytestmark = pytest.mark.full

NZ_HOST     = os.environ.get("NZ_DEV_HOST",     "127.0.0.1")
NZ_PORT     = int(os.environ.get("NZ_DEV_PORT",  "5480"))
NZ_DB       = os.environ.get("NZ_DEV_DB",        "JUST_DATA")
NZ_USER     = os.environ.get("NZ_DEV_USER",      "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD",  "password")

TABLE_FROM = "JUST_DATA..DIMDATE"

CONSISTENCY_CASES = [
    pytest.param("true::BOOLEAN", id="BOOLEAN"),
    pytest.param("'2024-12-11'::DATE", id="DATE"),
    pytest.param("123.456::NUMERIC(10,3)", id="NUMERIC_10_3"),
    pytest.param("3.1400::NUMERIC(10,4)", id="NUMERIC_10_4"),
    pytest.param("15::BYTEINT", id="BYTEINT"),
    pytest.param("25000::SMALLINT", id="SMALLINT"),
    pytest.param("12345678::INTEGER", id="INTEGER"),
    pytest.param("9223372036854775807::BIGINT", id="BIGINT_max"),
    pytest.param("3.14::FLOAT", id="FLOAT"),
    pytest.param("3.14::DOUBLE PRECISION", id="DOUBLE"),
    pytest.param("'abc'::VARCHAR(10)", id="VARCHAR"),
    pytest.param("'abc'::NCHAR(10)", id="NCHAR"),
    pytest.param("'abc'::NVARCHAR(10)", id="NVARCHAR"),
    pytest.param("'2024-12-11 14:30:00'::TIMESTAMP", id="TIMESTAMP"),
    pytest.param("'05:41:15'::TIME", id="TIME"),
    pytest.param("'2 years 5 hours 11 months 41 minutes 15 sec'::INTERVAL", id="INTERVAL_complex"),
    pytest.param("'5 hours 41 minutes  15 sec'::INTERVAL", id="INTERVAL_simple"),
    pytest.param("NULL", id="NULL"),
    pytest.param("NULL::INTEGER", id="NULL_INTEGER"),
    pytest.param("NULL::VARCHAR(10)", id="NULL_VARCHAR"),
]


def _normalize(val):
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.isoformat()
    if isinstance(val, datetime.timedelta):
        total = int(val.total_seconds())
        days = total // 86400
        remainder = total % 86400
        hours = remainder // 3600
        minutes = (remainder % 3600) // 60
        seconds = remainder % 60
        return f"{days} days {hours:02d}:{minutes:02d}:{seconds:02d}"
    if isinstance(val, decimal.Decimal):
        return str(val)
    if isinstance(val, str):
        return val.strip()
    return str(val)


def _to_number(s):
    try:
        if '.' in s or 'e' in s or 'E' in s:
            return float(s)
        return int(s)
    except (ValueError, TypeError):
        return None
    if isinstance(val, str):
        return val.strip()
    return val


async def _conn():
    return await nzpy.connect(
        user=NZ_USER, password=NZ_PASSWORD,
        host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
    )


@pytest.mark.parametrize("expr", CONSISTENCY_CASES)
@pytest.mark.asyncio
async def test_from_vs_no_from(expr):
    query_from = f"SELECT {expr} FROM {TABLE_FROM} LIMIT 1"
    query_no_from = f"SELECT {expr}"

    conn = await _conn()
    try:
        cur_from = conn.cursor()
        cur_no = conn.cursor()
        try:
            await cur_from.execute(query_from)
            row_from = await cur_from.fetchone()
            assert row_from is not None, f"No row from: {query_from}"

            await cur_no.execute(query_no_from)
            row_no = await cur_no.fetchone()
            assert row_no is not None, f"No row from: {query_no_from}"

            val_from = _normalize(row_from[0])
            val_no = _normalize(row_no[0])

            if isinstance(val_from, str) and isinstance(val_no, str):
                n_from = _to_number(val_from)
                n_no = _to_number(val_no)
                if n_from is not None and n_no is not None:
                    if abs(n_from - n_no) < 1e-6:
                        val_from = n_from
                        val_no = n_no

            if isinstance(val_from, float) and isinstance(val_no, float):
                assert abs(val_from - val_no) < 1e-6, (
                    f"FROM={val_from!r} vs NO_FROM={val_no!r} for {expr}"
                )
            else:
                assert val_from == val_no, (
                    f"FROM={val_from!r} vs NO_FROM={val_no!r} for {expr}"
                )

            assert type(val_from) is type(val_no) or (
                isinstance(val_from, (int, float)) and isinstance(val_no, (int, float))
            ), (
                f"Type mismatch: FROM={type(row_from[0]).__name__} vs NO_FROM={type(row_no[0]).__name__} for {expr}"
            )
        finally:
            await cur_from.close()
            await cur_no.close()
    finally:
        await conn.close()


KNOWN_INCONSISTENCIES = [
    pytest.param("3.14::REAL", id="REAL_precision"),
]


@pytest.mark.parametrize("expr", KNOWN_INCONSISTENCIES)
@pytest.mark.asyncio
async def test_known_from_vs_no_from_inconsistency(expr):
    query_from = f"SELECT {expr} FROM {TABLE_FROM} LIMIT 1"
    query_no_from = f"SELECT {expr}"

    conn = await _conn()
    try:
        cur_from = conn.cursor()
        cur_no = conn.cursor()
        try:
            await cur_from.execute(query_from)
            row_from = await cur_from.fetchone()
            await cur_no.execute(query_no_from)
            row_no = await cur_no.fetchone()

            assert row_from is not None
            assert row_no is not None

            val_from = row_from[0]
            val_no = row_no[0]

            assert val_from is not None or val_no is None, (
                f"Expected at least one non-None: FROM={val_from!r} NO_FROM={val_no!r}"
            )

            from_repr = repr(val_from)
            no_repr = repr(val_no)
            type_from = type(val_from).__name__
            type_no = type(val_no).__name__

            print(f"\n  {expr}:")
            print(f"    FROM:     {from_repr}  (type={type_from})")
            print(f"    NO_FROM:  {no_repr}  (type={type_no})")
        finally:
            await cur_from.close()
            await cur_no.close()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_to_char_consistency():
    conn = await _conn()
    try:
        cur_from = conn.cursor()
        cur_no = conn.cursor()
        try:
            await cur_from.execute(
                f"SELECT TO_CHAR(NOW(), 'YYYY-MM-DD HH24') FROM {TABLE_FROM} LIMIT 1"
            )
            row_from = await cur_from.fetchone()

            await cur_no.execute("SELECT TO_CHAR(NOW(), 'YYYY-MM-DD HH24')")
            row_no = await cur_no.fetchone()

            val_from = _normalize(row_from[0])
            val_no = _normalize(row_no[0])

            assert val_from == val_no, (
                f"TO_CHAR FROM={val_from!r} vs NO_FROM={val_no!r}"
            )
        finally:
            await cur_from.close()
            await cur_no.close()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_multi_column_from_vs_no_from():
    expr = "1::INTEGER, 3.14::DOUBLE, 'abc'::VARCHAR(10), NULL, true::BOOLEAN"
    query_from = f"SELECT {expr} FROM {TABLE_FROM} LIMIT 1"
    query_no_from = f"SELECT {expr}"

    conn = await _conn()
    try:
        cur_from = conn.cursor()
        cur_no = conn.cursor()
        try:
            await cur_from.execute(query_from)
            row_from = await cur_from.fetchone()

            await cur_no.execute(query_no_from)
            row_no = await cur_no.fetchone()

            for i, (v_from, v_no) in enumerate(zip(row_from, row_no)):
                n_from = _normalize(v_from)
                n_no = _normalize(v_no)
                assert n_from == n_no, (
                    f"Col {i}: FROM={n_from!r} vs NO_FROM={n_no!r}"
                )
                assert type(n_from) is type(n_no), (
                    f"Col {i}: type FROM={type(n_from).__name__} vs NO_FROM={type(n_no).__name__}"
                )
        finally:
            await cur_from.close()
            await cur_no.close()
    finally:
        await conn.close()