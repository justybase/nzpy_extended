import sys
import time

sys.path.insert(0, '.')
import nzpy_extended.sync as nzpy


def main():
    conn = nzpy.connect(
        user="admin",
        password="password",
        host="127.0.0.1",
        port=5480,
        database="JUST_DATA",
    )
    try:
        cur = conn.cursor()
        cur.execute("CREATE TEMP TABLE demo_timeout (id INT)")
        cur.execute("INSERT INTO demo_timeout VALUES (42)")

        # --- Timeout per connection (pyodbc-compatible) ---
        conn.timeout = 10.0
        c = conn.cursor()
        print(f"Cursor inherits connection timeout: {c.timeout}s")

        # --- Timeout per cursor ---
        c.timeout = 30.0
        print(f"Cursor-specific timeout: {c.timeout}s")

        # --- Timeout per execute (highest priority) ---
        # 1. explicit argument > 2. cursor.timeout > 3. conn.timeout
        cur.execute("SELECT pg_sleep(1)", timeout=10.0)
        row = cur.fetchone()
        print("Query completed before timeout")

        # --- Timeout that triggers ---
        print("Executing query with 3s timeout...")
        start = time.monotonic()
        try:
            cur.execute("SELECT pg_sleep(999)", timeout=3.0)
            cur.fetchall()
        except nzpy.OperationalError as e:
            elapsed = time.monotonic() - start
            print(f"Timed out after {elapsed:.2f}s: {e}")

        # --- Session survives timeout ---
        cur.execute("SELECT id FROM demo_timeout")
        row = cur.fetchone()
        print(f"Temp table survived timeout: id={row[0]}")

        # --- Disable timeout ---
        cur.timeout = 0  # 0 = no limit
        conn.timeout = None  # None = no limit
        cur.execute("SELECT 1")
        print("Query with no timeout:", cur.fetchone()[0])

    finally:
        conn.close()


if __name__ == "__main__":
    main()
