import asyncio
import sys
sys.path.insert(0, '.')
import nzpy_extended as nzpy


async def main():
    conn = await nzpy.connect(
        user="admin",
        password="password",
        host="127.0.0.1",
        port=5480,
        database="JUST_DATA",
    )
    try:
        cur = conn.cursor()
        await cur.execute(
            "SELECT 1 AS num UNION ALL SELECT 2 UNION ALL SELECT 3"
        )
        async for row in cur:
            print(row)
    finally:
        await conn.close()


asyncio.run(main())
