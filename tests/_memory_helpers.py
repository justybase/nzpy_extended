"""Shared helpers for RAM / object-count memory regression tests."""

from __future__ import annotations

import gc
import os

import pytest

pytest.importorskip("psutil")

import psutil  # noqa: E402  # after importorskip

# SQL – FACTPRODUCTINVENTORY has 7 columns
SQL_FACTPRODUCT = "select * from JUST_DATA..FACTPRODUCTINVENTORY limit 100000"
ROW_LIST_LEN = 7

# Thresholds (tune if the test environment changes)
# native-ext baseline Decimal fluctuation: 3146–3755; threshold 4500 => ~700 margin.
MAX_DECIMALS = 4500
MAX_ROW_LISTS = 4500

# fetchall: 5 iterations; per-iter RSS rate allows arena warmup (iter 0–1)
MAX_RSS_GROWTH_FETCHALL_MB = 50.0
MAX_RSS_GROWTH_PER_ITER_FETCHALL_MB = 6.0

# cancel: tighter bounds (fewer objects per iteration)
MAX_RSS_GROWTH_CANCEL_MB = 30.0
MAX_RSS_GROWTH_PER_ITER_CANCEL_MB = 2.0

# Steady-state check for the close-without-cancel loop. Raw RSS climbs during
# warm-up and is flat from about iteration 50 on (probe: iterations 0-9 grow
# 37 MB, 50-59 grow 0.0 MB, 60-69 grow 0.1 MB). The test runs the warm-up first
# and asserts only on the measured window, so a real leak still shows up at a
# limit about 10x above the measured steady state.
WARMUP_ITERATIONS_STEADY = 50
MAX_STEADY_GROWTH_MB = 5.0
MAX_STEADY_GROWTH_PER_ITER_MB = 0.5

FETCH_ROWS_DEFAULT = 5
ITERATIONS_FETCHALL = 5
ITERATIONS_CANCEL = 10
ITERATIONS_MULTIPLE_CANCEL = 20
ITERATIONS_POOL = 15
FETCHMANY_SIZE = 1000

# Max allowed RSS growth difference between async and sync (isolated subprocesses).
MAX_ASYNC_SYNC_GROWTH_DELTA_MB = 10.0

_ISOLATED_RSS_WORKER = r'''
import asyncio, gc, json, os, sys
sys.path.insert(0, sys.argv[1])
from tests._memory_helpers import collect_snapshot
api, mode, iters, fetch_rows, sql, native_on = (
    sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6], sys.argv[7] == "1"
)
import nzpy_extended._native as _native
_native._HAVE_NATIVE = native_on
NZ = dict(
    user=os.environ.get("NZ_DEV_USER", "admin"),
    password=os.environ.get("NZ_DEV_PASSWORD", "password"),
    database=os.environ.get("NZ_DEV_DB", "JUST_DATA"),
    host=os.environ.get("NZ_DEV_HOST", "127.0.0.1"),
    port=int(os.environ.get("NZ_DEV_PORT", "5480")),
)
gc.collect()
rss = []
if api == "async":
    import nzpy_extended as nzpy
    async def main():
        conn = await nzpy.connect(**NZ)
        try:
            for _ in range(iters):
                cur = conn.cursor()
                await cur.execute(sql)
                if mode == "fetchall":
                    rows = await cur.fetchall()
                    del rows
                else:
                    for _ in range(fetch_rows):
                        await cur.fetchone()
                    await conn.cancel()
                await cur.close()
                r, _, _ = collect_snapshot()
                rss.append(r)
        finally:
            await conn.close()
    asyncio.run(main())
else:
    import nzpy_extended.sync as nzpy_sync
    conn = nzpy_sync.connect(**NZ)
    try:
        for _ in range(iters):
            cur = conn.cursor()
            cur.execute(sql)
            if mode == "fetchall":
                rows = cur.fetchall()
                del rows
            else:
                cur.fetchmany(fetch_rows)
                conn.cancel()
            cur.close()
            r, _, _ = collect_snapshot()
            rss.append(r)
    finally:
        conn.close()
print(json.dumps(rss))
'''


def count_leaked_objects(row_list_len: int = ROW_LIST_LEN) -> tuple[int, int]:
    """Count Decimal objects and row-sized lists currently alive."""
    decimals = 0
    row_lists = 0
    for obj in gc.get_objects():
        tn = type(obj).__name__
        if tn == "Decimal":
            decimals += 1
        elif tn == "list" and len(obj) == row_list_len:
            row_lists += 1
    return decimals, row_lists


def rss_mb() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / 1_000_000


def collect_snapshot() -> tuple[float, int, int]:
    gc.collect()
    dec, rl = count_leaked_objects()
    return rss_mb(), dec, rl


def set_native(enabled: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    import nzpy_extended._native as _native

    monkeypatch.setattr(_native, "_HAVE_NATIVE", enabled)


def assert_memory_stable(
    rss_per_iter: list[float],
    *,
    dec_final: int,
    rl_final: int,
    max_decimals: int = MAX_DECIMALS,
    max_row_lists: int = MAX_ROW_LISTS,
    max_rss_growth_mb: float = MAX_RSS_GROWTH_FETCHALL_MB,
    max_rss_growth_per_iter_mb: float = MAX_RSS_GROWTH_PER_ITER_FETCHALL_MB,
    iterations: int,
    label: str = "",
) -> None:
    """Assert object counts and RSS trend stay within acceptable bounds."""
    prefix = f"{label}: " if label else ""

    assert dec_final < max_decimals, (
        f"{prefix}Decimal leak: {dec_final} alive (limit {max_decimals}) "
        f"after {iterations} iterations"
    )
    assert rl_final < max_row_lists, (
        f"{prefix}Row-list leak: {rl_final} alive (limit {max_row_lists}) "
        f"after {iterations} iterations"
    )

    if len(rss_per_iter) < 2:
        return

    rss_growth = rss_per_iter[-1] - rss_per_iter[0]
    # Skip first iteration for rate – large first fetch warms CPython arenas / caches.
    if len(rss_per_iter) >= 3:
        rss_per_iter_rate = (rss_per_iter[-1] - rss_per_iter[1]) / (len(rss_per_iter) - 2)
    else:
        rss_per_iter_rate = rss_growth / max(len(rss_per_iter) - 1, 1)

    assert rss_growth < max_rss_growth_mb, (
        f"{prefix}RSS growth {rss_growth:.1f} MB exceeds limit {max_rss_growth_mb} MB "
        f"over {iterations} iterations (first={rss_per_iter[0]:.1f}, last={rss_per_iter[-1]:.1f})"
    )
    assert rss_per_iter_rate < max_rss_growth_per_iter_mb, (
        f"{prefix}RSS growth rate {rss_per_iter_rate:.2f} MB/iter exceeds limit "
        f"{max_rss_growth_per_iter_mb} MB/iter over {iterations} iterations"
    )


def run_isolated_rss_benchmark(
    api: str,
    mode: str,
    iterations: int,
    sql: str,
    *,
    fetch_rows: int = FETCH_ROWS_DEFAULT,
    native_on: bool = True,
    project_root: str | None = None,
) -> list[float]:
    """Run memory workload in a fresh subprocess (fair async vs sync comparison)."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = project_root or str(Path(__file__).resolve().parents[1])
    out = subprocess.check_output(
        [
            sys.executable,
            "-c",
            _ISOLATED_RSS_WORKER,
            root,
            api,
            mode,
            str(iterations),
            str(fetch_rows),
            sql,
            "1" if native_on else "0",
        ],
        text=True,
    )
    return json.loads(out.strip().splitlines()[-1])
