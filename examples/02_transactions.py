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
        conn.autocommit = False
        cur = conn.cursor()

        await cur.execute("CREATE TEMP TABLE demo (id INT, label VARCHAR(50))")
        await cur.execute("INSERT INTO demo VALUES (?, ?)", (1, "alpha"))
        await cur.execute("INSERT INTO demo VALUES (?, ?)", (2, "beta"))
        await conn.commit()
        print("Committed alpha, beta")

        conn.autocommit = True  # switch back to autocommit
        await cur.execute("INSERT INTO demo VALUES (?, ?)", (3, "gamma"))
        print("Auto-committed gamma")

        await cur.execute("SELECT * FROM demo ORDER BY id")
        for row in await cur.fetchall():
            print(row)
    finally:
        await conn.close()


asyncio.run(main())
