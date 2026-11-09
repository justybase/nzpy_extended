import asyncio
import sys
from datetime import date
from decimal import Decimal
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

        # --- Example 1: Auto-infer columns from data ---
        try:
            await cur.execute("DROP TABLE load_auto1")
        except Exception:
            pass
        rows = [
            (1, "Alice", 100.50),
            (2, "Bob", 200.75),
            (3, "Carol", 999.99),
        ]
        count = await nzpy.load_data(conn, "load_auto1", rows)
        print(f"[Example 1] Auto-infer columns, inserted {count} rows")

        await cur.execute("SELECT COUNT(*) FROM load_auto1")
        total = (await cur.fetchone())[0]
        print(f"  COUNT(*) = {total}")

        await cur.execute("SELECT * FROM load_auto1 ORDER BY col1")
        for row in await cur.fetchall():
            print(f"  {row}")

        # --- Example 2: Mixed types with auto-infer ---
        try:
            await cur.execute("DROP TABLE load_auto2")
        except Exception:
            pass
        rows2 = [
            (10, "item_a", Decimal("19.99"), True, date(2025, 1, 15)),
            (20, "item_b", Decimal("49.99"), False, date(2025, 6, 1)),
        ]
        count = await conn.load_data("load_auto2", rows2)
        print(f"\n[Example 2] Mixed types auto-infer, inserted {count} rows")

        await cur.execute("SELECT COUNT(*) FROM load_auto2")
        total = (await cur.fetchone())[0]
        print(f"  COUNT(*) = {total}")

        await cur.execute("SELECT * FROM load_auto2 ORDER BY col1")
        for row in await cur.fetchall():
            print(f"  {row}")

        # --- Example 3: Generator with auto-infer ---
        def generate_rows(n):
            for i in range(n):
                yield (i, f"row_{i}", f"{i + 0.5}")

        count = await nzpy.load_data(conn, "load_auto1", rows=generate_rows(100))
        print(f"\n[Example 3] Generator with auto-infer, inserted {count} rows")

        await cur.execute("SELECT COUNT(*) FROM load_auto1")
        total = (await cur.fetchone())[0]
        print(f"  COUNT(*) = {total}")

        # --- Example 4: Explicit columns (no auto-infer) ---
        try:
            await cur.execute("DROP TABLE load_explicit")
        except Exception:
            pass
        count = await conn.load_data(
            table_name="load_explicit",
            rows=[
                (1, "explicit", "99.99"),
            ],
            columns=[
                ("id", "INT"),
                ("label", "VARCHAR(100)"),
                ("price", "NUMERIC(12,2)"),
            ],
        )
        print(f"\n[Example 4] Explicit columns, inserted {count} rows")

        await cur.execute("SELECT COUNT(*) FROM load_explicit")
        total = (await cur.fetchone())[0]
        print(f"  COUNT(*) = {total}")

        await cur.execute("SELECT * FROM load_explicit")
        for row in await cur.fetchall():
            print(f"  {row}")

        # --- Verify persisted tables ---
        print(f"\nTables persist: load_auto1, load_auto2, load_explicit")

    finally:
        await conn.close()


asyncio.run(main())
