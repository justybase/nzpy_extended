import os

import pytest

import nzpy_extended as nzpy
from nzpy_extended.utils import pg_to_py_encodings

pytestmark = pytest.mark.smoke

NZ_HOST = os.environ.get("NZ_DEV_HOST", "192.168.0.144")
NZ_PORT = int(os.environ.get("NZ_DEV_PORT", "5480"))
NZ_DB = os.environ.get("NZ_DEV_DB", "JUST_DATA")
NZ_USER = os.environ.get("NZ_DEV_USER", "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD", "password")


async def _connect(**kwargs):
    return await nzpy.connect(
        user=NZ_USER, password=NZ_PASSWORD,
        host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
        **kwargs,
    )


# ===== Unit: pg_to_py_encodings mapping =====


@pytest.mark.parametrize("pg_name, expected_py", [
    ("latin1", "iso8859-1"),
    ("latin2", "iso8859_2"),
    ("latin9", "iso8859_15"),
    ("unicode", "utf-8"),
    ("sql_ascii", "ascii"),
    ("win1250", "cp1250"),
    ("win1251", "cp1251"),
    ("win1252", "cp1252"),
])
def test_pg_encoding_maps_correctly(pg_name, expected_py):
    assert pg_to_py_encodings[pg_name] == expected_py


def test_pg_encoding_unknown_passes_through():
    assert pg_to_py_encodings.get("unknown_xyz", "unknown_xyz") == "unknown_xyz"


# ===== Default encoding (utf8, backward-compatible) =====


@pytest.mark.asyncio
async def test_default_client_encoding_is_utf8():
    conn = await _connect()
    try:
        assert conn._client_encoding in ("utf8", "utf-8"), \
            f"Unexpected _client_encoding: {conn._client_encoding!r}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_encoding_connect_works():
    conn = await _connect()
    try:
        cur = conn.cursor()
        await cur.execute("SELECT 1")
        row = await cur.fetchone()
        assert row[0] == 1
    finally:
        await conn.close()


# ===== ASCII round-trip (must work with default encoding) =====


@pytest.mark.asyncio
@pytest.mark.parametrize("value, col_type", [
    ("hello", "VARCHAR(50)"),
    ("world", "NVARCHAR(50)"),
    ("123", "VARCHAR(50)"),
])
async def test_ascii_roundtrip_default(value, col_type):
    conn = await _connect()
    try:
        cur = conn.cursor()
        table = f"TMP_ENC_ASC_{abs(hash(value))}"
        await cur.execute(f"DROP TABLE {table} IF EXISTS")
        await cur.execute(f"CREATE TEMP TABLE {table} (col {col_type})")
        await cur.execute(f"INSERT INTO {table} VALUES ('{value}')")
        await cur.execute(f"SELECT col FROM {table}")
        row = await cur.fetchone()
        assert row[0] == value
    finally:
        await conn.close()


# ===== Unicode (NVARCHAR) round-trip with default utf8 =====


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    "zażółć",
    "中文测试",
    "Привет мир",
])
async def test_unicode_nvarchar_default(value):
    conn = await _connect()
    try:
        cur = conn.cursor()
        table = f"TMP_ENC_UNI_{abs(hash(value))}"
        await cur.execute(f"DROP TABLE {table} IF EXISTS")
        await cur.execute(f"CREATE TEMP TABLE {table} (col NVARCHAR(100))")
        await cur.execute(f"INSERT INTO {table} VALUES ('{value}')")
        await cur.execute(f"SELECT col FROM {table}")
        row = await cur.fetchone()
        assert row[0] == value, f"NVARCHAR mismatch: {row[0]!r} != {value!r}"
    finally:
        await conn.close()


# ===== Latin-1 VARCHAR round-trip (chars that fit in both latin1 and utf8) =====


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    "Süßes Café",
    "Äpfel",
    "Straße München",
])
async def test_latin_varchar_default(value):
    conn = await _connect()
    try:
        cur = conn.cursor()
        table = f"TMP_ENC_LAT_{abs(hash(value))}"
        await cur.execute(f"DROP TABLE {table} IF EXISTS")
        await cur.execute(f"CREATE TEMP TABLE {table} (col VARCHAR(50))")
        await cur.execute(f"INSERT INTO {table} VALUES ('{value}')")
        await cur.execute(f"SELECT col FROM {table}")
        row = await cur.fetchone()
        assert row[0] == value, f"VARCHAR mismatch: {row[0]!r} != {value!r}"
    finally:
        await conn.close()


# ===== Explicit client_encoding parameter =====


@pytest.mark.asyncio
async def test_explicit_client_encoding_utf8_unicode():
    conn = await _connect(client_encoding="utf8")
    try:
        cur = conn.cursor()
        await cur.execute("DROP TABLE TMP_ENC_XU IF EXISTS")
        await cur.execute("CREATE TEMP TABLE TMP_ENC_XU (col NVARCHAR(100))")
        await cur.execute("INSERT INTO TMP_ENC_XU VALUES ('中文测试')")
        await cur.execute("SELECT col FROM TMP_ENC_XU")
        row = await cur.fetchone()
        assert row[0] == '中文测试'
        assert conn._client_encoding in ("utf8", "utf-8")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_explicit_client_encoding_latin9():
    conn = await _connect(client_encoding="latin9")
    try:
        cur = conn.cursor()
        await cur.execute("DROP TABLE TMP_ENC_XL IF EXISTS")
        await cur.execute("CREATE TEMP TABLE TMP_ENC_XL (col VARCHAR(50))")
        await cur.execute("INSERT INTO TMP_ENC_XL VALUES ('Süßes Café')")
        await cur.execute("SELECT col FROM TMP_ENC_XL")
        row = await cur.fetchone()
        assert row[0] == 'Süßes Café'
    finally:
        await conn.close()


# ===== C extension parity =====


@pytest.mark.asyncio
@pytest.mark.observe
@pytest.mark.parametrize("test_value, col_type", [
    ("Süßes Café", "VARCHAR(100)"),
    ("Hello World", "VARCHAR(100)"),
    ("中文", "NVARCHAR(100)"),
])
async def test_cext_parity_encoding(con_cext, cext_mode, test_value, col_type):
    conn = con_cext
    cur = conn.cursor()
    table = f"TMP_PARITY_{abs(hash(test_value))}"
    await cur.execute(f"DROP TABLE {table} IF EXISTS")
    await cur.execute(f"CREATE TEMP TABLE {table} (col {col_type})")
    await cur.execute(f"INSERT INTO {table} VALUES ('{test_value}')")
    await cur.execute(f"SELECT col FROM {table}")
    row = await cur.fetchone()
    assert row[0] == test_value, \
        f"[{cext_mode}] Mismatch: {row[0]!r} != {test_value!r}"
