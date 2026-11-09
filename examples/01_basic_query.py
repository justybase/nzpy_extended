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
        await cur.execute("SELECT version()")
        row = await cur.fetchone()
        print("Netezza version:", row[0])

        await cur.execute("SELECT 1 AS col1, 'hello' AS col2, 3.14 AS col3")
        rows = await cur.fetchall()
        for row in rows:
            print(row)
    finally:
        await conn.close()


asyncio.run(main())
