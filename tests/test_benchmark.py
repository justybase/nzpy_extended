import os
import time

import pytest

import nzpy_extended as nzpy

pytestmark = pytest.mark.benchmark

NZ_HOST     = os.environ.get("NZ_DEV_HOST",     "127.0.0.1")
NZ_PORT     = int(os.environ.get("NZ_DEV_PORT",  "5480"))
NZ_DB       = os.environ.get("NZ_DEV_DB",        "JUST_DATA")
NZ_USER     = os.environ.get("NZ_DEV_USER",      "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD",  "password")

ROW_COUNT = 10000

TEST_QUERIES = [
    pytest.param(
        "SELECT * FROM JUST_DATA.ADMIN.DIMDATE ORDER BY ROWID LIMIT " + str(ROW_COUNT),
        id="DIMDATE (10k rows)",
    ),
    pytest.param(
        "SELECT * FROM JUST_DATA.ADMIN.FACTPRODUCTINVENTORY ORDER BY ROWID LIMIT " + str(ROW_COUNT),
        id="FACTPRODUCTINVENTORY (10k rows)",
    ),
    pytest.param(
        """
        SELECT
            10::bigint, null::bigint, true::Boolean, false::Boolean,
            null::Boolean, 5::Byteint, null::Byteint, 'a'::Char,
            null::Char, current_date::Date, null::Date, 0.5::float,
            null::float, 10::integer, null::integer, '02:00:00'::TIME,
            'abc'::nchar(10), null::nchar(10), 1.54::numeric(30, 6),
            null::numeric(30, 6), 'abc'::Nvarchar(10), null::Nvarchar(10),
            1.54::real, null::real, 5::smallint, null::smallint,
            '10:12:13'::TIME, null::time,
            DATE_TRUNC('hour', current_timestamp)::Timestamp,
            null::Timestamp, 'abc'::varchar(10), null::varchar(10)
        FROM JUST_DATA..FACTPRODUCTINVENTORY
        ORDER BY ROWID ASC
        LIMIT """ + str(ROW_COUNT),
        id="Many Types (10k rows)",
    ),
]


def format_bytes(n):
    for unit in ["Bytes", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def format_time(ms):
    if ms < 1000:
        return f"{ms:.2f}ms"
    return f"{ms / 1000:.2f}s"


async def _nzpy_conn():
    return await nzpy.connect(
        user=NZ_USER, password=NZ_PASSWORD,
        host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
    )


@pytest.mark.parametrize("sql", TEST_QUERIES)
@pytest.mark.asyncio
async def test_benchmark_throughput(sql):
    conn = await _nzpy_conn()
    try:
        cur = conn.cursor()

        start = time.perf_counter()
        await cur.execute(sql)

        rows = 0
        data_size = 0
        while True:
            batch = await cur.fetchmany(1000)
            if not batch:
                break
            for row in batch:
                rows += 1
                for val in row:
                    if val is not None:
                        data_size += len(str(val).encode("utf-8"))

        elapsed = (time.perf_counter() - start) * 1000
        throughput = data_size / (elapsed / 1000) if elapsed > 0 else 0
        rows_per_sec = rows / (elapsed / 1000) if elapsed > 0 else 0

        print(f"\n  Time:      {format_time(elapsed)}")
        print(f"  Rows:      {rows:,}")
        print(f"  Data:      {format_bytes(data_size)}")
        print(f"  Throughput: {format_bytes(throughput)}/s")
        print(f"  Rows/sec:  {rows_per_sec:,.0f}")

        assert rows > 0, "Query must return at least one row"
    finally:
        await conn.close()
