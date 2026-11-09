import sys
sys.path.insert(0, '.')
import nzpy_extended.sync as nzpy
from nzpy_extended import SyncPool


def main():
    pool = SyncPool(
        min_size=1, max_size=5,
        user="admin", password="password",
        host="127.0.0.1", port=5480,
        database="JUST_DATA",
    )

    # --- Context manager ---
    with pool.connection() as conn:
        row = conn.execute("SELECT version()").fetchone()
        print("Pool connection:", row[0])

    # --- Acquire / release by hand ---
    conn = pool.acquire()
    conn.execute("SELECT 1")
    pool.release(conn)

    # --- Automatic rollback on release ---
    conn = pool.acquire()
    conn.autocommit = False
    conn.execute("CREATE TEMP TABLE demo_pool (x INT)")
    conn.execute("INSERT INTO demo_pool VALUES (99)")
    pool.release(conn)  # rolls back the uncommitted INSERT

    conn2 = pool.acquire()
    row = conn2.execute("SELECT COUNT(*) FROM demo_pool").fetchone()
    print(f"Pool rolled back open transaction: {row[0]} rows")  # 0

    # --- Stats ---
    stats = pool.get_stats()
    print(f"Pool stats: size={stats['pool_size']}, available={stats['pool_available']}, in_use={stats['pool_in_use']}")

    pool.close_all()
    print(f"Pool closed: {pool.get_stats()['pool_closed']}")


if __name__ == "__main__":
    main()
