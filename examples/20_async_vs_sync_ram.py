#!/usr/bin/env python
"""
Compare RSS growth: async API vs sync API on identical workloads.

IMPORTANT: always measures each API in a separate subprocess. Comparing
async then sync in one process is misleading because sync inherits a
warmed heap from async.

Usage:
    python examples/20_async_vs_sync_ram.py
    python examples/20_async_vs_sync_ram.py --mode cancel --iterations 10
    python examples/20_async_vs_sync_ram.py --native-off
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests._memory_helpers import (
    MAX_ASYNC_SYNC_GROWTH_DELTA_MB,
    SQL_FACTPRODUCT,
    run_isolated_rss_benchmark,
)


def main() -> int:
    p = argparse.ArgumentParser(description="Isolated async vs sync RAM comparison")
    p.add_argument("--mode", choices=("fetchall", "cancel"), default="fetchall")
    p.add_argument("--iterations", type=int, default=5)
    p.add_argument("--sql", default=SQL_FACTPRODUCT)
    p.add_argument("--fetch-rows", type=int, default=5)
    p.add_argument("--native-off", action="store_true", help="Disable native extension (pure Python)")
    args = p.parse_args()

    native_on = not args.native_off
    print(
        f"Isolated subprocess comparison  mode={args.mode}  iters={args.iterations}  "
        f"native={native_on}"
    )
    print(f"{'API':<8} {'first':>8} {'last':>8} {'growth':>8} {'peak':>8}")
    print("-" * 44)

    stats: dict[str, float] = {}
    for api in ("async", "sync"):
        rss = run_isolated_rss_benchmark(
            api, args.mode, args.iterations, args.sql,
            fetch_rows=args.fetch_rows, native_on=native_on,
        )
        growth = rss[-1] - rss[0]
        stats[api] = growth
        print(f"{api:<8} {rss[0]:8.1f} {rss[-1]:8.1f} {growth:+8.1f} {max(rss):8.1f}")

    delta = stats["async"] - stats["sync"]
    print()
    if abs(delta) < MAX_ASYNC_SYNC_GROWTH_DELTA_MB:
        print(f"VERDICT: Comparable (async-sync growth = {delta:+.1f} MB)")
        return 0
    if delta > 0:
        print(f"VERDICT: Async higher growth by {delta:+.1f} MB")
    else:
        print(f"VERDICT: Sync higher growth by {-delta:.1f} MB")
    return 1


if __name__ == "__main__":
    sys.exit(main())
