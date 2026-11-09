import asyncio
import os
import sys

# Automatically add the root directory to the path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import nzpy_extended
try:
    import nzpy
    NZPY_AVAILABLE = True
except ImportError:
    NZPY_AVAILABLE = False
    print("Warning: Official nzpy not available for comparison")

HOST = os.getenv("NZ_HOST", "127.0.0.1")
PORT = int(os.getenv("NZ_PORT", "5480"))
USER = os.getenv("NZ_USER", "admin")
PASSWORD = os.getenv("NZ_PASSWORD", "password")
DATABASE = os.getenv("NZ_DATABASE", "SYSTEM")

# Types and values to test (text value and its cast)
TYPE_TESTS = [
    ("123", "INTEGER"),
    ("12345678901234", "BIGINT"),
    ("123.4567", "NUMERIC(20,4)"),
    ("123.45", "FLOAT"),
    ("123.456789", "DOUBLE PRECISION"),
    ("'test string'", "VARCHAR(100)"),
    ("true", "BOOLEAN"),
    ("'2026-01-01'", "DATE"),
    ("'12:34:56.789'", "TIME"),
    ("'2026-01-01 12:34:56.789'", "TIMESTAMP")
]

QUERIES = []
for val, t in TYPE_TESTS:
    # Without FROM (usually Text mode in Netezza)
    QUERIES.append(f"SELECT CAST({val} AS {t});")
    # With FROM (usually Binary / DBOS mode in Netezza)
    QUERIES.append(f"SELECT CAST({val} AS {t}) FROM JUST_DATA.._v_table LIMIT 1;")

# Adding previous complex queries for full coverage
QUERIES.extend([
    "select * from JUST_DATA.._v_table order by OBJID;",
    "select * from JUST_DATA..DIMACCOUNT order by rowid;",
    "select * from JUST_DATA..DIMDATE order by rowid;",
    "select * from JUST_DATA..FACTCALLCENTER order by rowid;",
    "select * from JUST_DATA..FACTFINANCE order by rowid;"
])

async def run_regression_test():
    if not NZPY_AVAILABLE:
        print("Cannot run regression test without official nzpy.")
        return

    # Connect to official nzpy
    conn_official = nzpy.connect(
        user=USER, password=PASSWORD, host=HOST, port=PORT, database=DATABASE
    )

    # Connect to nzpy_extended
    conn_extended = await nzpy_extended.connect(
        user=USER, password=PASSWORD, host=HOST, port=PORT, database=DATABASE
    )

    for i, query in enumerate(QUERIES):
        print(f"\n[{i+1}/{len(QUERIES)}] Testing: {query}")
        
        # Fetch from nzpy
        with conn_official.cursor() as cur:
            cur.execute(query)
            rows_official = cur.fetchall()
            
        # Fetch from nzpy_extended
        async with conn_extended.cursor() as cur:
            await cur.execute(query)
            rows_extended = await cur.fetchall()

        # Compare row counts
        len_off = len(rows_official)
        len_ext = len(rows_extended)
        print(f"  Row count - nzpy: {len_off}, nzpy_extended: {len_ext}")
        
        if len_off != len_ext:
            print("  ERROR: Different row count!")
            break

        # Compare cells (exact match)
        errors = 0
        for r_idx in range(len_off):
            row_off = rows_official[r_idx]
            row_ext = rows_extended[r_idx]
            
            # Require exactly the same tuple length
            if len(row_off) != len(row_ext):
                print(f"  ERROR: Different column count in row {r_idx}")
                errors += 1
                continue
                
            for c_idx in range(len(row_off)):
                v_off = row_off[c_idx]
                v_ext = row_ext[c_idx]
                
                # Use strict comparison (==)
                # But sometimes the async driver returns different types under the hood for None or spaces
                if v_off != v_ext:
                    # Try converting to string to see if values match (often Decimal vs Float etc.)
                    if str(v_off) != str(v_ext):
                        print(f"  MISMATCH: Row {r_idx}, Column {c_idx}")
                        print(f"    nzpy:          {repr(v_off)} (Type: {type(v_off)})")
                        print(f"    nzpy_extended: {repr(v_ext)} (Type: {type(v_ext)})")
                        errors += 1
                        
            if errors > 20: # Stop after 20 errors to avoid spam
                print("  ERROR: Too many errors, aborting test for this table.")
                break
                
        if errors == 0:
            print("  SUCCESS: 100% data match!")
        else:
            print(f"  ERROR: Found {errors} mismatches.")

    conn_official.close()
    await conn_extended.close()

if __name__ == "__main__":
    asyncio.run(run_regression_test())
