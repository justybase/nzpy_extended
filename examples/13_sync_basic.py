import sys
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
        # --- Basic query ---
        cur = conn.cursor()
        cur.execute("SELECT version()")
        row = cur.fetchone()
        print("Netezza version:", row[0])

        # --- Method chaining (PEP 249) ---
        row = cur.execute("SELECT 1 AS col1, 'hello' AS col2, 3.14 AS col3").fetchone()
        print("Chained result:", row)

        # --- One-shot convenience (pyodbc pattern) ---
        row = conn.execute("SELECT 2 + 3").fetchone()
        print("One-shot result:", row[0])

        # --- Cursor iteration (batched, arraysize=100 by default) ---
        cur.execute("CREATE TEMP TABLE demo_basic (id INT)")
        for i in range(5):
            cur.execute("INSERT INTO demo_basic VALUES (?)", (i,))

        cur.execute("SELECT id FROM demo_basic ORDER BY id")
        for row in cur:
            print("Row:", row)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
