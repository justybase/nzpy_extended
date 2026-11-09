import asyncio
import time
import os
import sys
import statistics

sys.stdout.reconfigure(encoding='utf-8')

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import nzpy_extended

try:
    import nzpy
    NZPY_OK = True
except ImportError:
    NZPY_OK = False

try:
    import pyodbc
    PYODBC_OK = True
except ImportError:
    PYODBC_OK = False

HOST = os.getenv("NZ_HOST", "127.0.0.1")
PORT = int(os.getenv("NZ_PORT", "5480"))
USER = os.getenv("NZ_USER", "admin")
PASSWORD = os.getenv("NZ_PASSWORD", "password")
DATABASE = os.getenv("NZ_DATABASE", "SYSTEM")
TABLE = "JUST_DATA..FACTPRODUCTINVENTORY"

QUERIES = {
    "INT only": f"SELECT (RANDOM()*10000)::INT c1 FROM {TABLE} LIMIT %d",
    "INT+NUMERIC": f"SELECT (RANDOM()*10000)::INT c1, (RANDOM()*10000)::NUMERIC(20,4) c2 FROM {TABLE} LIMIT %d",
    "INT+NVARCHAR": f"SELECT (RANDOM()*10000)::INT c1, (RANDOM()*10000)::NVARCHAR(50) c2 FROM {TABLE} LIMIT %d",
    "INT+FLOAT": f"SELECT (RANDOM()*10000)::INT c1, (RANDOM()*10000)::FLOAT c2 FROM {TABLE} LIMIT %d",
    "INT+TIME": f"SELECT (RANDOM()*10000)::INT c1, CURRENT_TIMESTAMP::TIME c2 FROM {TABLE} LIMIT %d",
    "INT+DATE": f"SELECT (RANDOM()*10000)::INT c1, CURRENT_TIMESTAMP::DATE c2 FROM {TABLE} LIMIT %d",
    "INT+BOOL": f"SELECT (RANDOM()*10000)::INT c1, CASE WHEN RANDOM()>0.5 THEN TRUE ELSE FALSE END c2 FROM {TABLE} LIMIT %d",
    "FULL (3 cols)": f"SELECT (RANDOM()*10000)::INT c1, (RANDOM()*10000)::NUMERIC(20,4) c2, (RANDOM()*10000)::NVARCHAR(50) c3 FROM {TABLE} LIMIT %d",
    "BIG (10 cols)": f"SELECT (RANDOM()*10000)::INT c1, (RANDOM()*10000)::NUMERIC(20,4) c2, (RANDOM()*10000)::NVARCHAR(50) c3, (RANDOM()*10000)::INT c4, (RANDOM()*10000)::INT c5, (RANDOM()*10000)::INT c6, (RANDOM()*10000)::INT c7, (RANDOM()*10000)::INT c8, (RANDOM()*10000)::INT c9, (RANDOM()*10000)::INT c10 FROM {TABLE} LIMIT %d",
}

ROWS = 100000
RUNS = 5

BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
ENDC = "\033[0m"


def fmt(t):
    if t >= 1:
        return f"{t:.4f}s"
    elif t >= 0.001:
        return f"{t*1000:.2f}ms"
    else:
        return f"{t*1e6:.0f}us"


def bar(val, max_val, width=40):
    if max_val == 0:
        return "-" * width
    n = int((val / max_val) * width)
    return "#" * n + "-" * (width - n)


async def run_nzpy_extended(q, rows, conn=None):
    close_conn = False
    if conn is None:
        conn = await nzpy_extended.connect(user=USER, password=PASSWORD, host=HOST, port=PORT, database=DATABASE)
        close_conn = True
    try:
        async with conn.cursor() as cur:
            t0 = time.perf_counter()
            await cur.execute(q)
            data = await cur.fetchall()
            t = time.perf_counter() - t0
        return len(data), t
    finally:
        if close_conn:
            await conn.close()


def run_nzpy(q, rows):
    conn = nzpy.connect(user=USER, password=PASSWORD, host=HOST, port=PORT, database=DATABASE)
    try:
        with conn.cursor() as cur:
            t0 = time.perf_counter()
            cur.execute(q)
            data = cur.fetchall()
            t = time.perf_counter() - t0
        return len(data), t
    finally:
        conn.close()


def run_pyodbc(q, rows):
    conn_str = f"DRIVER={{NetezzaSQL}};SERVER={HOST};PORT={PORT};DATABASE={DATABASE};UID={USER};PWD={PASSWORD};"
    conn = pyodbc.connect(conn_str)
    try:
        cur = conn.cursor()
        t0 = time.perf_counter()
        cur.execute(q)
        data = cur.fetchall()
        t = time.perf_counter() - t0
        cur.close()
        return len(data), t
    finally:
        conn.close()


async def experiment_column_types():
    print(f"\n{BOLD}=== EXPERIMENT 1: Column type impact on performance ==={ENDC}")
    print(f"  Rows: {ROWS}, Runs: {RUNS}, Connection: shared\n")
    print(f"{'Column types':<22} {'Rows/s':>10} {'Time':>12} {' vs INT ':>10}")
    print("-" * 60)

    conn = await nzpy_extended.connect(user=USER, password=PASSWORD, host=HOST, port=PORT, database=DATABASE)

    int_rps = None
    results = {}

    for name, template in QUERIES.items():
        times = []
        for _ in range(RUNS):
            cnt, dur = await run_nzpy_extended(template % ROWS, ROWS, conn)
            times.append(dur)
        avg = statistics.mean(times)
        rps = ROWS / avg
        results[name] = (ROWS, avg, rps)

        if name == "INT only":
            int_rps = rps

        speedup = f"{rps/int_rps:.2f}x" if int_rps and name != "INT only" else "-"
        color = GREEN if rps > 50000 else YELLOW if rps > 30000 else RED
        print(f"  {color}{name:<22} {rps:>10.0f} {fmt(avg):>12} {speedup:>10}{ENDC}")

    await conn.close()
    print()
    return results


async def experiment_statistical():
    print(f"\n{BOLD}=== EXPERIMENT 2: Statistical stability (FULL 3 cols) ==={ENDC}")
    print(f"  Rows: {ROWS}, Runs: {RUNS}\n")

    q = QUERIES["FULL (3 cols)"] % ROWS

    for label, fn, is_async in [
        ("nzpy_extended", run_nzpy_extended, True),
        ("official nzpy", run_nzpy, False) if NZPY_OK else None,
        ("pyodbc", run_pyodbc, False) if PYODBC_OK else None,
    ]:
        if label is None:
            continue
        times = []
        for r in range(RUNS):
            if is_async:
                cnt, dur = await fn(q, ROWS)
            else:
                cnt, dur = fn(q, ROWS)
            times.append(dur)
        avg = statistics.mean(times)
        stdev = statistics.stdev(times) if len(times) > 1 else 0
        rps_avg = ROWS / avg
        rps_stdev = ROWS / (avg - stdev) - ROWS / avg if stdev < avg else 0
        print(f"  {label:<20}: {ROWS} rows")
        print(f"    Time:     min={fmt(min(times))}, max={fmt(max(times))}, avg={fmt(avg)}, stddev={fmt(stdev)}")
        print(f"    Rows/s: {rps_avg:.0f} ± {rps_stdev:.0f}")
        print(f"    Range:    {ROWS/max(times):.0f} – {ROWS/min(times):.0f} rows/s")
        print()

    return


async def experiment_scaling():
    print(f"\n{BOLD}=== EXPERIMENT 3: Scaling (FULL 3 cols, varying LIMIT) ==={ENDC}")
    print(f"  Runs: {RUNS}, Connection: fresh per LIMIT\n")

    limits = [1000, 5000, 10000, 50000, 100000]
    q_name = "FULL (3 cols)"

    print(f"{'LIMIT':>8} {'rows/s':>10} {'Time':>12} {'us/row':>10}")
    print("-" * 45)

    for limit in limits:
        q = QUERIES[q_name] % limit
        times = []
        for _ in range(RUNS):
            cnt, dur = await run_nzpy_extended(q, limit)
            times.append(dur)
        avg = statistics.mean(times)
        rps = limit / avg
        us_per_row = avg / limit * 1e6
        color = GREEN if rps > 50000 else YELLOW
        print(f"  {color}{limit:>8} {rps:>10.0f} {fmt(avg):>12} {us_per_row:>9.1f}us{ENDC}")
    print()
    return


async def experiment_driver_comparison():
    if not NZPY_OK and not PYODBC_OK:
        print("  No official nzpy or pyodbc for comparison")
        return

    print(f"\n{BOLD}=== EXPERIMENT 4: Driver comparison ==={ENDC}")
    print(f"  Rows: {ROWS}, Runs: {RUNS}\n")

    tests = ["INT only", "INT+NUMERIC", "INT+NVARCHAR", "FULL (3 cols)", "BIG (10 cols)"]
    drivers = {"nzpy_extended": (run_nzpy_extended, True)}
    if NZPY_OK:
        drivers["official nzpy"] = (run_nzpy, False)
    if PYODBC_OK:
        drivers["pyodbc"] = (run_pyodbc, False)

    header = f"{'Test':<20}"
    for d in drivers:
        header += f" {d:>18}"
    print(header)
    print("-" * (20 + 18 * len(drivers)))

    for name in tests:
        q = QUERIES[name] % ROWS
        line = f"  {name:<18}"
        best = 0
        row_data = []
        for dlabel, (fn, is_async) in drivers.items():
            times = []
            for _ in range(RUNS):
                if is_async:
                    cnt, dur = await fn(q, ROWS)
                else:
                    cnt, dur = fn(q, ROWS)
                times.append(dur)
            avg = statistics.mean(times)
            rps = ROWS / avg
            row_data.append((dlabel, rps, avg))
            if rps > best:
                best = rps

        for dlabel, rps, avg in row_data:
            color = GREEN if rps >= best * 0.9 else YELLOW if rps >= best * 0.5 else RED
            ratio = f"{rps/best:.2f}x" if best > 0 else "-"
            line += f"  {color}{rps:>8.0f} ({ratio}){ENDC}"

        print(line)
    print()
    return


async def experiment_warm_vs_cold():
    print(f"\n{BOLD}=== EXPERIMENT 5: Cold vs warm connection ==={ENDC}")
    print(f"  Rows: {ROWS}, FULL 3 cols\n")

    q = QUERIES["FULL (3 cols)"] % ROWS

    print(f"  {GREEN}Cold connections (fresh connect each run):{ENDC}")
    cold_times = []
    for r in range(RUNS):
        cnt, dur = await run_nzpy_extended(q, ROWS)
        cold_times.append(dur)
        print(f"    Run {r+1}: {fmt(dur)} ({ROWS/dur:.0f} rows/s)")

    print(f"\n  {GREEN}Warm connection (shared, same connection):{ENDC}")
    conn = await nzpy_extended.connect(user=USER, password=PASSWORD, host=HOST, port=PORT, database=DATABASE)
    warm_times = []
    for r in range(RUNS):
        cnt, dur = await run_nzpy_extended(q, ROWS, conn)
        warm_times.append(dur)
        print(f"    Run {r+1}: {fmt(dur)} ({ROWS/dur:.0f} rows/s)")
    await conn.close()

    cold_avg = statistics.mean(cold_times)
    warm_avg = statistics.mean(warm_times)
    print(f"\n  Cold avg:  {fmt(cold_avg)} ({ROWS/cold_avg:.0f} rows/s)")
    print(f"  Warm avg: {fmt(warm_avg)} ({ROWS/warm_avg:.0f} rows/s)")
    diff = (cold_avg - warm_avg) / cold_avg * 100
    print(f"  Difference:        {diff:.1f}%")
    print()
    return


async def main():
    print(f"{BOLD}{'='*60}{ENDC}")
    print(f"{BOLD}   COMPREHENSIVE PERFORMANCE ANALYSIS nzpy_extended{ENDC}")
    print(f"{BOLD}{'='*60}{ENDC}")
    print(f"  Host: {HOST}:{PORT}, DB: {DATABASE}")
    print(f"  Official nzpy: {'OK' if NZPY_OK else 'N/A'},  pyodbc: {'OK' if PYODBC_OK else 'N/A'}")
    print(f"  Rows/run: {ROWS}, Runs/test: {RUNS}")

    await experiment_column_types()
    await experiment_statistical()
    await experiment_scaling()
    await experiment_driver_comparison()
    await experiment_warm_vs_cold()

    print(f"{BOLD}{'='*60}{ENDC}")
    print(f"   END")
    print(f"{BOLD}{'='*60}{ENDC}")


if __name__ == "__main__":
    asyncio.run(main())
