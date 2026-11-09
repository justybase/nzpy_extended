import asyncio
import sys
sys.path.insert(0, '.')
import nzpy_extended as nzpy
from nzpy_extended.pool import NzPool


async def main():
    pool = NzPool(
        min_size=2,
        max_size=5,
        user="admin",
        password="password",
        host="127.0.0.1",
        port=5480,
        database="JUST_DATA",
    )
    async with pool:
        async with pool.connection() as conn:
            cur = conn.cursor()
            await cur.execute("SELECT 42 AS answer")
            row = await cur.fetchone()
            print("Answer:", row[0])

        results = await asyncio.gather(*[
            _query(pool, i) for i in range(5)
        ])
        for r in results:
            print(r)


async def _query(pool, i):
    async with pool.connection() as conn:
        cur = conn.cursor()
        await cur.execute("SELECT ? AS val", (i * 10,))
        row = await cur.fetchone()
        return f"Query {i}: {row[0]}"


asyncio.run(main())
