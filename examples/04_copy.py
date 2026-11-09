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
        await cur.execute("""
            CREATE TEMP TABLE bulk_demo (id INT, name VARCHAR(50), amount NUMERIC(10,2))
        """)

        params = [
            (1, "Alice", "100.50"),
            (2, "Bob", "200.75"),
            (3, "Carol", "50.00"),
        ]
        await cur.executemany(
            "INSERT INTO bulk_demo VALUES (?, ?, ?)", params,
        )
        print(f"Inserted {cur.rowcount} rows")

        await cur.execute("SELECT * FROM bulk_demo ORDER BY id")
        for row in await cur.fetchall():
            print(row)
    finally:
        await conn.close()


asyncio.run(main())
