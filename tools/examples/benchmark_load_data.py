#!/usr/bin/env python3
"""Live benchmark of the bulk-load path: ``load_data`` versus ``executemany``.

The script creates uniquely named tables on the Netezza appliance and times:

* ``executemany`` (one ``INSERT`` per row) on a small sample of rows, reported
  per row, because it is far too slow to run at full size;
* ``load_data`` with a list of rows (the rows are already in memory);
* ``load_data`` with a generator (the rows are produced while they stream).

Each load creates its table, so every timing includes ``CREATE TABLE``. The
row count of the target table is checked after each load. Every table is
dropped in a ``finally`` block and the drop is reported.

Connection settings come from NZ_DEV_HOST, NZ_DEV_PORT, NZ_DEV_DATABASE,
NZ_DEV_USER and NZ_DEV_PASSWORD. There are no credential defaults. Run only
against a disposable database.

Example::

    python tools/examples/benchmark_load_data.py --rows 100000 --rounds 3 --output load_data.json
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import random
import statistics
import sys
import time
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

REQUIRED_ENV = ("NZ_DEV_HOST", "NZ_DEV_PORT", "NZ_DEV_DATABASE", "NZ_DEV_USER", "NZ_DEV_PASSWORD")

COLUMNS: list[tuple[str, str]] = [
    ("id", "INTEGER"),
    ("name", "VARCHAR(40)"),
    ("amount", "NUMERIC(12,2)"),
    ("created", "DATE"),
    ("active", "BOOLEAN"),
]
BASE_DATE = dt.date(2020, 1, 1)


def _gen_rows(count: int, seed: int) -> Iterator[tuple[Any, ...]]:
    """Deterministic rows with a realistic mix of types (no NULLs, so counts are exact)."""
    rng = random.Random(seed)
    for i in range(count):
        cents = rng.randrange(0, 10_000_000)
        yield (
            i,
            f"customer_{i % 50_000:06d}",
            Decimal(cents).scaleb(-2),
            BASE_DATE + dt.timedelta(days=rng.randrange(0, 2000)),
            rng.random() < 0.5,
        )


def _summary(samples: list[float], rows: int) -> dict[str, Any]:
    median = statistics.median(samples)
    return {
        "samples_s": [round(s, 3) for s in samples],
        "median_s": round(median, 3),
        "rows": rows,
        "rows_per_s": round(rows / median) if median > 0 else None,
    }


async def _create(cur: Any, table: str) -> None:
    columns = ", ".join(f"{name} {kind}" for name, kind in COLUMNS)
    await cur.execute(f"CREATE TABLE {table} ({columns}) DISTRIBUTE ON RANDOM")


async def _count(cur: Any, table: str) -> int:
    await cur.execute(f"SELECT COUNT(*) FROM {table}")
    return int((await cur.fetchone())[0])


async def _benchmark(args: argparse.Namespace) -> dict[str, Any]:
    import nzpy_extended as nzpy

    suffix = uuid.uuid4().hex[:8].upper()
    tables = {
        "executemany": f"ZZ_LDBENCH_EM_{suffix}",
        "load_list": f"ZZ_LDBENCH_LST_{suffix}",
        "load_generator": f"ZZ_LDBENCH_GEN_{suffix}",
    }
    result: dict[str, Any] = {
        "rows": args.rows,
        "executemany_rows": args.executemany_rows,
        "rounds": args.rounds,
        "python": sys.version.split()[0],
        "started": dt.datetime.now().isoformat(timespec="seconds"),
    }
    conn = await nzpy.connect(
        user=os.environ["NZ_DEV_USER"],
        password=os.environ["NZ_DEV_PASSWORD"],
        host=os.environ["NZ_DEV_HOST"],
        port=int(os.environ["NZ_DEV_PORT"]),
        database=os.environ["NZ_DEV_DATABASE"],
    )
    cur = conn.cursor()
    created: list[str] = []
    try:
        # executemany: one INSERT per row, measured on a small sample.
        await _create(cur, tables["executemany"])
        created.append(tables["executemany"])
        sample = list(_gen_rows(args.executemany_rows, args.seed))
        started = time.perf_counter()
        await cur.executemany(f"INSERT INTO {tables['executemany']} VALUES (?, ?, ?, ?, ?)", sample)
        em_elapsed = time.perf_counter() - started
        em_count = await _count(cur, tables["executemany"])
        if em_count != args.executemany_rows:
            raise RuntimeError(f"executemany stored {em_count} rows; expected {args.executemany_rows}")
        result["executemany"] = {
            "elapsed_s": round(em_elapsed, 3),
            "rows": args.executemany_rows,
            "per_row_ms": round(em_elapsed / args.executemany_rows * 1000, 2),
        }
        print(f"executemany: {args.executemany_rows} rows in {em_elapsed:.2f}s", file=sys.stderr, flush=True)

        # load_data with a list (rows materialised before the timer starts).
        load_rows = list(_gen_rows(args.rows, args.seed))
        samples: dict[str, list[float]] = {"load_list": [], "load_generator": []}
        for round_number in range(args.rounds):
            for label in ("load_list", "load_generator"):
                table = f"{tables[label]}_{round_number}"
                created.append(table)
                source: Any = load_rows if label == "load_list" else _gen_rows(args.rows, args.seed)
                started = time.perf_counter()
                returned = await conn.load_data(table, source, columns=COLUMNS)
                elapsed = time.perf_counter() - started
                stored = await _count(cur, table)
                if returned != args.rows or stored != args.rows:
                    raise RuntimeError(f"{label}: returned {returned}, stored {stored}; expected {args.rows}")
                await cur.execute(f"DROP TABLE {table}")
                created.remove(table)
                samples[label].append(elapsed)
                print(f"round {round_number + 1}/{args.rounds} {label}: {elapsed:.2f}s",
                      file=sys.stderr, flush=True)
        result["load_list"] = _summary(samples["load_list"], args.rows)
        result["load_generator"] = _summary(samples["load_generator"], args.rows)
        per_row_load = result["load_list"]["median_s"] / args.rows
        result["per_row_speedup_load_list_vs_executemany"] = round(
            (em_elapsed / args.executemany_rows) / per_row_load, 1
        ) if per_row_load > 0 else None
    finally:
        dropped: list[str] = []
        failed: list[str] = []
        for table in list(created):
            try:
                await cur.execute(f"DROP TABLE {table}")
                dropped.append(table)
            except Exception as exc:  # noqa: BLE001 - reported in the result, not hidden
                failed.append(f"{table}: {exc}")
        result["dropped"] = dropped if not failed else f"FAILED: {failed}"
        await conn.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=100_000, help="rows per load_data run (default 100000)")
    parser.add_argument("--executemany-rows", type=int, default=1000,
                        help="rows for the executemany baseline (default 1000)")
    parser.add_argument("--rounds", type=int, default=3, help="rounds of list and generator loads (default 3)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", type=Path, help="also write the JSON report here")
    args = parser.parse_args()

    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"missing required environment variables: {', '.join(missing)}")
    if min(args.rows, args.executemany_rows, args.rounds) <= 0:
        raise SystemExit("--rows, --executemany-rows and --rounds must be positive")

    report = asyncio.run(_benchmark(args))
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if isinstance(report.get("dropped"), list) else 1


if __name__ == "__main__":
    raise SystemExit(main())
