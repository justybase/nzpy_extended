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
        cur = conn.cursor()
        cur.execute("CREATE TEMP TABLE demo_tx (id INT, label VARCHAR(50))")

        # --- Autocommit off ---
        conn.autocommit = False
        cur.execute("INSERT INTO demo_tx VALUES (?, ?)", (1, "alpha"))
        cur.execute("INSERT INTO demo_tx VALUES (?, ?)", (2, "beta"))
        conn.commit()
        print("Committed alpha, beta")

        # --- Autocommit on ---
        conn.autocommit = True
        cur.execute("INSERT INTO demo_tx VALUES (?, ?)", (3, "gamma"))
        print("Auto-committed gamma")

        # --- Rollback on error ---
        conn.autocommit = False
        cur.execute("INSERT INTO demo_tx VALUES (?, ?)", (4, "delta"))
        conn.rollback()
        conn.autocommit = True
        print("Rolled back delta")

        # --- Transaction context manager ---
        with conn.transaction():
            cur.execute("INSERT INTO demo_tx VALUES (?, ?)", (5, "epsilon"))
        print("Committed via transaction() context manager")
        # If an exception occurs inside the block, conn.rollback() is called

        # --- Verify ---
        cur.execute("SELECT id, label FROM demo_tx ORDER BY id")
        for row in cur.fetchall():
            print(row)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
