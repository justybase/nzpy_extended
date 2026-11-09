#!/usr/bin/env python
"""
Example: efficient two-pass CSV import for large files.

Problem:
  load_data() with create_if_missing=True and no explicit columns
  materialises ALL rows in memory to infer types — not feasible for huge CSVs.

Solution (two-pass):
  1. Sample first N rows → infer column types (budget)
  2. Stream the full file through load_data() with explicit columns
     (inference skipped, generator streamed without materialisation)

Usage:
  export NZ_DEV_HOST=... NZ_DEV_PORT=... NZ_DEV_DB=... \
         NZ_DEV_USER=... NZ_DEV_PASSWORD=...
  python examples/11_import_large_csv.py
"""

import csv
import os
import sys
import pathlib
import tempfile
from itertools import islice

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import nzpy_extended as nzpy
from nzpy_extended.utils import _infer_columns_from_rows

SAMPLE_SIZE = 1000


def generate_csv(path, num_rows):
    """Generate a CSV with mixed columns: date, int, float, text, bool."""
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f, delimiter='|')
        for i in range(num_rows):
            date = f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
            # int col stays < SAMPLE_SIZE so sample-based inference is safe
            small_int = i % SAMPLE_SIZE
            writer.writerow([
                date,
                small_int,
                f"{small_int}.{(small_int * 7) % 100:02d}",
                f"row_{i}",
                "true" if i % 2 == 0 else "false",
            ])


def sample_rows(path, n):
    """Read first N rows from a CSV file (memory-efficient)."""
    rows = []
    with open(path, newline='') as f:
        reader = csv.reader(f, delimiter='|')
        for row in islice(reader, n):
            rows.append(tuple(row))
    return rows


def row_generator(path):
    """
    Yield rows one by one without loading the whole file.

    The generator is consumed lazily by load_data() — only one row
    is held in memory at a time during the CSV serialisation step.
    """
    with open(path, newline='') as f:
        reader = csv.reader(f, delimiter='|')
        for row in reader:
            yield tuple(row)


async def main():
    # --- Setup: generate a large CSV ---
    TOTAL_ROWS = 50_000
    csv_path = os.path.join(tempfile.gettempdir(), 'large_import_example.csv')
    print(f"Generating {TOTAL_ROWS} rows → {csv_path}")
    generate_csv(csv_path, TOTAL_ROWS)

    conn = await nzpy.connect(
        user=os.environ.get("NZ_DEV_USER", "admin"),
        password=os.environ.get("NZ_DEV_PASSWORD", "password"),
        host=os.environ.get("NZ_DEV_HOST", "127.0.0.1"),
        port=int(os.environ.get("NZ_DEV_PORT", "5480")),
        database=os.environ.get("NZ_DEV_DB", "JUST_DATA"),
    )

    try:
        cur = conn.cursor()

        # =================================================================
        # Two-pass import
        # =================================================================
        print("\n--- Two-pass import ---")

        table = "import_two_pass"
        await cur.execute(f"DROP TABLE {table} IF EXISTS")

        # Pass 1: sample first N rows to infer column types
        #         (memory = SAMPLE_SIZE rows, not TOTAL_ROWS)
        # NOTE: sampling is inherently risky for range-bound types (INT ranges).
        #       If the sample doesn't span the full value range, inferred types
        #       may be too narrow (e.g. SMALLINT vs INT). Increase sample size
        #       or pass columns manually as a safety measure.
        print(f"  Pass 1: sampling {SAMPLE_SIZE} rows for type inference...")
        sample = sample_rows(csv_path, SAMPLE_SIZE)
        inferred_columns = _infer_columns_from_rows(sample)
        print(f"          Inferred columns: {inferred_columns}")

        # Pass 2: stream the full file (generator, no materialisation)
        print(f"  Pass 2: streaming {TOTAL_ROWS} rows (generator)...")
        count = await nzpy.load_data(
            conn, table,
            rows=row_generator(csv_path),
            columns=inferred_columns,
            create_if_missing=True,
            encoding='LATIN9',
        )
        print(f"          Inserted {count} rows")

        await cur.execute(f"SELECT COUNT(*) FROM {table}")
        total = (await cur.fetchone())[0]
        print(f"          COUNT(*)  = {total}")
        assert total == TOTAL_ROWS

        # =================================================================
        # Compare with one-pass (all-rows-materialised) — for reference
        # =================================================================
        print("\n--- One-pass (all-in-memory) import ---")

        table2 = "import_one_pass"
        await cur.execute(f"DROP TABLE {table2} IF EXISTS")

        all_rows = []
        with open(csv_path, newline='') as f:
            reader = csv.reader(f, delimiter='|')
            for row in reader:
                all_rows.append(tuple(row))

        print(f"  Loaded {len(all_rows)} rows into memory (list)")
        count2 = await nzpy.load_data(conn, table2, all_rows)
        print(f"  Inserted {count2} rows")

        await cur.execute(f"SELECT COUNT(*) FROM {table2}")
        total2 = (await cur.fetchone())[0]
        print(f"  COUNT(*)  = {total2}")
        assert total2 == TOTAL_ROWS

        # Verify both tables have identical data
        await cur.execute(
            f"SELECT COUNT(*) FROM (SELECT * FROM {table} "
            f"MINUS SELECT * FROM {table2}) x"
        )
        diff = (await cur.fetchone())[0]
        print(f"\n  Diff between two-pass and one-pass: {diff} rows")
        assert diff == 0, "Data mismatch between methods!"

        print("\n✓ Two-pass import produces identical results.")

        # Cleanup
        await cur.execute(f"DROP TABLE {table} IF EXISTS")
        await cur.execute(f"DROP TABLE {table2} IF EXISTS")

    finally:
        await conn.close()

    os.unlink(csv_path)


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
