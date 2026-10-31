#!/usr/bin/env python
"""
Memory leak test for nzpy_extended.

Detects reference leaks by running a large query multiple times and
checking that Python objects (Decimals, lists) and RSS do not accumulate
across iterations.  Runs with AND without the native extension so both
paths are covered.

Usage:
    # default: 10 iterations, async API, fetchall, both native-ext and pure-python
    python examples/19_memory_leak_test.py

    # cancel after partial fetch (same SQL repeated, abort each time)
    python examples/19_memory_leak_test.py --mode cancel --fetch-rows 5 --iterations 20

    # control settings
    python examples/19_memory_leak_test.py --iterations 25 --sql "select * from JUST_DATA..FACTPRODUCTINVENTORY limit 100000" --sync

    # quick smoke
    python examples/19_memory_leak_test.py --iterations 3 --sql "select * from JUST_DATA..FACTPRODUCTINVENTORY limit 10000"

RAM notes:
    - Sync API uses the same async engine via _SyncRunner (background thread).
    - Compare async vs sync RAM only in separate processes (examples/20_async_vs_sync_ram.py).
    - Acceptable growth: fetchall <50 MB total / <6 MB/iter; cancel <30 MB / <2 MB/iter.

Environment variables (same as tests):
    NZ_DEV_HOST, NZ_DEV_PORT, NZ_DEV_DB, NZ_DEV_USER, NZ_DEV_PASSWORD
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import os
import sys
import time
from pathlib import Path
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Add project root so nzpy_extended can be imported
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Connection defaults – mirrors conftest.py / existing examples
# ---------------------------------------------------------------------------
_NZ_HOST = os.environ.get("NZ_DEV_HOST", "192.168.0.144")
_NZ_PORT = int(os.environ.get("NZ_DEV_PORT", "5480"))
_NZ_DB = os.environ.get("NZ_DEV_DB", "JUST_DATA")
_NZ_USER = os.environ.get("NZ_DEV_USER", "admin")
_NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD", "password")

_DEFAULT_SQL = "select * from JUST_DATA..FACTPRODUCTINVENTORY limit 100000"


def _fmt_mb(val: float) -> str:
    return f"{val:>8.1f}"


def _count_leaked_objects() -> tuple[int, int]:
    """Count Decimal objects and row-sized lists (len=7 for FACTPRODUCTINVENTORY)."""
    decimals = 0
    row_lists = 0
    for obj in gc.get_objects():
        tn = type(obj).__name__
        if tn == "Decimal":
            decimals += 1
        elif tn == "list" and len(obj) == 7:
            row_lists += 1
    return decimals, row_lists


def _get_rss() -> float:
    import psutil
    return psutil.Process(os.getpid()).memory_info().rss / 1_000_000


# ---------------------------------------------------------------------------
# Test runner – shared logic
# ---------------------------------------------------------------------------

def _analyse_and_report(
    measurements: list[float],
    decimals_per_iter: list[int],
    iterations: int,
    native_label: str,
    row_counts: list[int],
    *,
    mode: Literal["fetchall", "cancel"],
) -> bool:
    rss_first = measurements[0]
    rss_last = measurements[-1]
    d_first = decimals_per_iter[0]
    d_last = decimals_per_iter[-1]
    growth_rss = rss_last - rss_first
    growth_dec = d_last - d_first
    if len(measurements) >= 3:
        rss_rate = (rss_last - measurements[1]) / (len(measurements) - 2)
    else:
        rss_rate = growth_rss / max(len(measurements) - 1, 1)

    max_rss_growth = 200.0 if mode == "fetchall" else 30.0
    max_rss_rate = 6.0 if mode == "fetchall" else 2.0
    max_dec_rate = 100.0

    passed = True
    verdict = "STABLE"

    if iterations >= 3:
        early_dec = sum(decimals_per_iter[:3]) / 3
        late_dec = sum(decimals_per_iter[-3:]) / 3
        dec_growth_rate = (late_dec - early_dec) / max(iterations, 1)

        if dec_growth_rate > max_dec_rate:
            passed = False
            verdict = f"LEAK  (Decimals growing ~{dec_growth_rate:.0f}/iter)"
        elif growth_rss > max_rss_growth:
            passed = False
            verdict = f"LEAK  (RSS growth {growth_rss:.1f} MB)"
        elif rss_rate > max_rss_rate:
            passed = False
            verdict = f"LEAK  (RSS rate {rss_rate:.2f} MB/iter)"
        else:
            verdict = "STABLE"
    else:
        verdict = "STABLE (too few iterations)"

    print(f"\nResults ({native_label}, {mode}):")
    print(f"  RSS       : first={rss_first:.1f} MB  last={rss_last:.1f} MB  growth={growth_rss:+.1f} MB")
    print(f"  RSS rate  : {rss_rate:+.2f} MB/iter")
    print(f"  Decimals  : first={d_first}  last={d_last}  growth={growth_dec:+d}")
    print(f"  Verdict   : {verdict}")
    print()

    return passed


# ---------------------------------------------------------------------------
# Iteration bodies
# ---------------------------------------------------------------------------

async def _async_fetchall_iter(conn: Any, sql: str) -> tuple[int, float]:
    t0 = time.perf_counter()
    cur = conn.cursor()
    await cur.execute(sql)
    rows = await cur.fetchall()
    rc = len(rows)
    elapsed = time.perf_counter() - t0
    del rows
    await cur.close()
    del cur
    return rc, elapsed


async def _async_cancel_iter(conn: Any, sql: str, fetch_rows: int) -> tuple[int, float]:
    t0 = time.perf_counter()
    cur = conn.cursor()
    await cur.execute(sql)
    read = 0
    for _ in range(fetch_rows):
        row = await cur.fetchone()
        if row is None:
            break
        read += 1
    await conn.cancel()
    await cur.close()
    del cur
    elapsed = time.perf_counter() - t0
    return read, elapsed


def _sync_fetchall_iter(conn: Any, sql: str) -> tuple[int, float]:
    t0 = time.perf_counter()
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    rc = len(rows)
    elapsed = time.perf_counter() - t0
    del rows
    cur.close()
    del cur
    return rc, elapsed


def _sync_cancel_iter(conn: Any, sql: str, fetch_rows: int) -> tuple[int, float]:
    t0 = time.perf_counter()
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchmany(fetch_rows)
    read = len(rows)
    del rows
    conn.cancel()
    cur.close()
    del cur
    elapsed = time.perf_counter() - t0
    return read, elapsed


# ---------------------------------------------------------------------------
# Test runner – async
# ---------------------------------------------------------------------------

async def _run_async(
    sql: str,
    iterations: int,
    delay: float,
    native_label: str,
    mode: Literal["fetchall", "cancel"],
    fetch_rows: int,
) -> bool:
    import nzpy_extended as nzpy

    conn = await nzpy.connect(
        user=_NZ_USER, password=_NZ_PASSWORD,
        host=_NZ_HOST, port=_NZ_PORT, database=_NZ_DB,
    )
    try:
        return await _run_async_loop(conn, sql, iterations, delay, native_label, mode, fetch_rows)
    finally:
        await conn.close()


async def _run_async_loop(
    conn: Any,
    sql: str,
    iterations: int,
    delay: float,
    native_label: str,
    mode: Literal["fetchall", "cancel"],
    fetch_rows: int,
) -> bool:
    measurements: list[float] = []
    decimals_per_iter: list[int] = []
    row_counts: list[int] = []

    header = (
        f"\n{'='*60}\n"
        f"Mode: {native_label} (async, {mode})\n"
        f"SQL : {sql[:80]}{'...' if len(sql) > 80 else ''}\n"
        f"Iters: {iterations}\n"
        f"{'='*60}\n"
    )
    print(header, flush=True)
    print(f"{'#':>4} | {'RSS (MB)':>10} | {'Decimals':>9} | {'RowLists':>9} | {'Rows':>8} | {'Time':>6}")
    print("-" * 55)

    for i in range(iterations):
        if mode == "fetchall":
            rc, elapsed = await _async_fetchall_iter(conn, sql)
        else:
            rc, elapsed = await _async_cancel_iter(conn, sql, fetch_rows)

        gc.collect()
        rss = _get_rss()
        d, rl = _count_leaked_objects()

        measurements.append(rss)
        decimals_per_iter.append(d)
        row_counts.append(rc)

        print(f"{i:>4} | {_fmt_mb(rss):>10} | {d:>9} | {rl:>9} | {rc:>8} | {elapsed:.1f}s", flush=True)

        if delay and i < iterations - 1:
            await asyncio.sleep(delay)

    return _analyse_and_report(
        measurements, decimals_per_iter, iterations, native_label, row_counts, mode=mode
    )


# ---------------------------------------------------------------------------
# Test runner – sync
# ---------------------------------------------------------------------------

def _run_sync(
    sql: str,
    iterations: int,
    delay: float,
    native_label: str,
    mode: Literal["fetchall", "cancel"],
    fetch_rows: int,
) -> bool:
    import nzpy_extended.sync as nzpy_sync

    conn = nzpy_sync.connect(
        user=_NZ_USER, password=_NZ_PASSWORD,
        host=_NZ_HOST, port=_NZ_PORT, database=_NZ_DB,
    )
    try:
        return _run_sync_loop(conn, sql, iterations, delay, native_label, mode, fetch_rows)
    finally:
        conn.close()


def _run_sync_loop(
    conn: Any,
    sql: str,
    iterations: int,
    delay: float,
    native_label: str,
    mode: Literal["fetchall", "cancel"],
    fetch_rows: int,
) -> bool:
    measurements: list[float] = []
    decimals_per_iter: list[int] = []
    row_counts: list[int] = []

    header = (
        f"\n{'='*60}\n"
        f"Mode: {native_label} (sync, {mode})\n"
        f"SQL : {sql[:80]}{'...' if len(sql) > 80 else ''}\n"
        f"Iters: {iterations}\n"
        f"{'='*60}\n"
    )
    print(header, flush=True)
    print(f"{'#':>4} | {'RSS (MB)':>10} | {'Decimals':>9} | {'RowLists':>9} | {'Rows':>8} | {'Time':>6}")
    print("-" * 55)

    for i in range(iterations):
        if mode == "fetchall":
            rc, elapsed = _sync_fetchall_iter(conn, sql)
        else:
            rc, elapsed = _sync_cancel_iter(conn, sql, fetch_rows)

        gc.collect()
        rss = _get_rss()
        d, rl = _count_leaked_objects()

        measurements.append(rss)
        decimals_per_iter.append(d)
        row_counts.append(rc)

        print(f"{i:>4} | {_fmt_mb(rss):>10} | {d:>9} | {rl:>9} | {rc:>8} | {elapsed:.1f}s", flush=True)

        if delay and i < iterations - 1:
            time.sleep(delay)

    return _analyse_and_report(
        measurements, decimals_per_iter, iterations, native_label, row_counts, mode=mode
    )


# ---------------------------------------------------------------------------
# Mode switcher
# ---------------------------------------------------------------------------

def _set_native(enabled: bool) -> None:
    import nzpy_extended._native as _native
    _native._HAVE_NATIVE = enabled


def run_test(
    sql: str = _DEFAULT_SQL,
    iterations: int = 10,
    delay: float = 0.2,
    use_sync: bool = False,
    mode: Literal["fetchall", "cancel"] = "fetchall",
    fetch_rows: int = 5,
) -> bool:
    """Run test in both native-ext and pure-python modes. Returns True if both pass."""
    results: list[bool] = []

    for native_enabled, native_label in [(True, "native extension"), (False, "pure Python")]:
        _set_native(native_enabled)
        gc.collect()

        print(f"\nInitial RSS: {_get_rss():.1f} MB  |  "
              f"Decimals: {_count_leaked_objects()[0]}  |  "
              f"native-ext: {native_enabled}", flush=True)

        if use_sync:
            ok = _run_sync(sql, iterations, delay, native_label, mode, fetch_rows)
        else:
            ok = asyncio.run(_run_async(sql, iterations, delay, native_label, mode, fetch_rows))
        results.append(ok)

    print()
    print("=" * 60)
    if all(results):
        print("OVERALL: PASS - No memory leak detected in either mode.")
        print(f"         Tested with: {sql}")
        print(f"         Mode: {mode}  Iterations: {iterations}")
        return True
    print("OVERALL: FAIL - Memory leak detected!")
    for label, ok in zip(["native extension", "pure Python"], results):
        print(f"         {label}: {'PASS' if ok else 'FAIL'}")
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="nzpy_extended memory leak test")
    parser.add_argument("--sql", default=_DEFAULT_SQL, help="SQL query to execute")
    parser.add_argument("--iterations", type=int, default=10, help="Number of query iterations")
    parser.add_argument("--delay", type=float, default=0.2, help="Seconds between iterations")
    parser.add_argument("--sync", action="store_true", help="Use sync API instead of async")
    parser.add_argument(
        "--mode",
        choices=("fetchall", "cancel"),
        default="fetchall",
        help="fetchall = full read; cancel = partial fetch then abort",
    )
    parser.add_argument(
        "--fetch-rows",
        type=int,
        default=5,
        help="Rows to read before cancel (only with --mode cancel)",
    )
    args = parser.parse_args()

    print("nzpy_extended Memory Leak Test")
    print("=" * 60)
    print(f"SQL        : {args.sql}")
    print(f"Iterations : {args.iterations}")
    print(f"API        : {'sync' if args.sync else 'async'}")
    print(f"Mode       : {args.mode}")
    if args.mode == "cancel":
        print(f"Fetch rows : {args.fetch_rows}")
    print("=" * 60)

    passed = run_test(
        sql=args.sql,
        iterations=args.iterations,
        delay=args.delay,
        use_sync=args.sync,
        mode=args.mode,
        fetch_rows=args.fetch_rows,
    )

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
