import asyncio
import time
import sys
sys.path.insert(0, '.')
import nzpy_extended as nzpy


def generate_rows(n):
    for i in range(n):
        yield (i, f"row_{i}", f"{i % 1000}.{i % 100:02d}")


async def main():
    N = 50000

    conn = await nzpy.connect(
        user="admin",
        password="password",
        host="127.0.0.1",
        port=5480,
        database="JUST_DATA",
    )
    try:
        cur = conn.cursor()

        try:
            await cur.execute("DROP TABLE load_bench")
        except Exception:
            pass

        await cur.execute("""
            CREATE TABLE load_bench (
                id INT,
                name VARCHAR(100),
                amount NUMERIC(15,2)
            ) DISTRIBUTE ON RANDOM
        """)

        rows = generate_rows(N)

        start = time.perf_counter()
        count = await nzpy.load_data(conn, "load_bench", rows)
        elapsed = time.perf_counter() - start

        await cur.execute("SELECT COUNT(*) FROM load_bench")
        total = (await cur.fetchone())[0]

        rows_per_sec = count / elapsed if elapsed > 0 else 0

        print(f"Rows inserted : {count}")
        print(f"COUNT(*)      : {total}")
        print(f"Time          : {elapsed:.3f} s")
        print(f"Throughput    : {rows_per_sec:,.0f} rows/s")
        print(f"\nTable load_bench persists in the database.")

    finally:
        await conn.close()


asyncio.run(main())
