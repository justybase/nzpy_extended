#!/usr/bin/env python3
"""Interleaved live benchmark: native (Rust) versus pure-Python DBOS decoding.

The script creates a uniquely named table on the Netezza appliance, fills it
with rows of appliance-representative types, and then times ``SELECT`` of the
whole table with the native decoder and with the pure-Python decoder in
alternating rounds, inside one process. The table is dropped in a ``finally``
block and the drop is reported.

Connection settings come from NZ_DEV_HOST, NZ_DEV_PORT, NZ_DEV_DATABASE,
NZ_DEV_USER and NZ_DEV_PASSWORD. There are no credential defaults. Run only
against a disposable database.

Example::

    python tools/examples/benchmark_native_vs_python.py --doublings 9 --rounds 5

With 300 generated rows and 9 doublings the table holds 153,600 rows.
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
from pathlib import Path
from typing import Any

REQUIRED_ENV = ("NZ_DEV_HOST", "NZ_DEV_PORT", "NZ_DEV_DATABASE", "NZ_DEV_USER", "NZ_DEV_PASSWORD")

COLUMNS: list[tuple[str, str]] = [
    ("c_bool", "BOOLEAN"),
    ("c_i1", "BYTEINT"),
    ("c_i2", "SMALLINT"),
    ("c_i4", "INTEGER"),
    ("c_i8", "BIGINT"),
    ("c_f4", "REAL"),
    ("c_f8", "DOUBLE PRECISION"),
    ("c_n1", "NUMERIC(1,0)"),
    ("c_n4", "NUMERIC(4,2)"),
    ("c_n9", "NUMERIC(9,3)"),
    ("c_n10", "NUMERIC(10,4)"),
    ("c_n15", "NUMERIC(15,5)"),
    ("c_n18", "NUMERIC(18,6)"),
    ("c_n19", "NUMERIC(19,4)"),
    ("c_n28", "NUMERIC(28,10)"),
    ("c_n38", "NUMERIC(38,20)"),
    ("c_d", "DATE"),
    ("c_t", "TIME"),
    ("c_ts", "TIMESTAMP"),
    ("c_tz", "TIME WITH TIME ZONE"),
    ("c_iv", "INTERVAL"),
    ("c_ch", "CHAR(5)"),
    ("c_vc", "VARCHAR(40)"),
    ("c_nch", "NCHAR(6)"),
    ("c_nvc", "NVARCHAR(30)"),
]


def _lit_num(rng: random.Random, precision: int, scale: int) -> str:
    if rng.random() < 0.1:
        return "NULL"
    int_digits = precision - scale
    digits = "".join(rng.choice("0123456789") for _ in range(int_digits)) or "0"
    frac = "".join(rng.choice("0123456789") for _ in range(scale))
    sign = "-" if rng.random() < 0.5 else ""
    text = f"{sign}{digits.lstrip('0') or '0'}"
    return text + (f".{frac}" if scale else "")


def _lit_text(rng: random.Random, max_len: int, alphabet: str) -> str:
    text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, max_len)))
    return "'" + text.replace("'", "''") + "'"


def _gen_row(rng: random.Random) -> str:
    def maybe(make: Any) -> str:
        return "NULL" if rng.random() < 0.1 else make()

    ddate = dt.date(1, 1, 1) + dt.timedelta(days=rng.randint(0, 3_000_000))
    ts = dt.datetime(1900, 1, 1) + dt.timedelta(microseconds=rng.randint(0, 9_000_000_000_000_000))
    tm = dt.time(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59), rng.randint(0, 999_999))
    zone_sign = "+" if rng.random() < 0.5 else "-"
    zone = f"{zone_sign}{rng.randint(0, 12):02d}:{rng.choice(['00', '30'])}"
    tz_literal = f"CAST('{tm.strftime('%H:%M:%S.%f')}{zone}' AS TIME WITH TIME ZONE)"
    interval = (
        f"INTERVAL '{rng.randint(-999, 999)} {rng.randint(0, 23):02d}:"
        f"{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}'"
    )
    ascii_alphabet = "abcXYZ 09é"
    unicode_alphabet = "abcżółćé λ日本"
    values = [
        maybe(lambda: rng.choice(["TRUE", "FALSE"])),
        maybe(lambda: str(rng.randint(-128, 127))),
        maybe(lambda: str(rng.randint(-32768, 32767))),
        maybe(lambda: str(rng.randint(-2**31, 2**31 - 1))),
        maybe(lambda: str(rng.randint(-2**63, 2**63 - 1))),
        maybe(lambda: repr(float(rng.uniform(-1e6, 1e6)))),
        maybe(lambda: repr(float(rng.uniform(-1e12, 1e12)))),
    ]
    for precision, scale in [(1, 0), (4, 2), (9, 3), (10, 4), (15, 5), (18, 6), (19, 4), (28, 10), (38, 20)]:
        values.append(maybe(lambda p=precision, s=scale: _lit_num(rng, p, s)))
    values += [
        maybe(lambda: f"DATE '{ddate.isoformat()}'"),
        maybe(lambda: f"TIME '{tm.strftime('%H:%M:%S.%f')}'"),
        maybe(lambda: f"TIMESTAMP '{ts.strftime('%Y-%m-%d %H:%M:%S.%f')}'"),
        maybe(lambda: tz_literal),
        maybe(lambda: interval),
        maybe(lambda: _lit_text(rng, 5, ascii_alphabet)),
        maybe(lambda: _lit_text(rng, 40, unicode_alphabet)),
        maybe(lambda: _lit_text(rng, 6, unicode_alphabet)),
        maybe(lambda: _lit_text(rng, 30, unicode_alphabet)),
    ]
    return "(" + ", ".join(values) + ")"


def _summary(samples: list[float]) -> dict[str, Any]:
    return {
        "median_s": round(statistics.median(samples), 3),
        "min_s": round(min(samples), 3),
        "all_s": [round(value, 3) for value in samples],
    }


async def _benchmark(args: argparse.Namespace) -> dict[str, Any]:
    import nzpy_extended as nzpy
    from nzpy_extended import _native

    if not _native._HAVE_NATIVE:
        raise SystemExit("the native extension is not loaded; there is nothing to compare")
    native_module = _native._native_ext
    original = (_native._HAVE_NATIVE, _native._native_ext)

    table = f"ZZ_NATIVE_BENCH_{uuid.uuid4().hex[:8].upper()}"
    result: dict[str, Any] = {"table": table, "rounds": args.rounds, "doublings": args.doublings}
    conn = await nzpy.connect(
        user=os.environ["NZ_DEV_USER"],
        password=os.environ["NZ_DEV_PASSWORD"],
        host=os.environ["NZ_DEV_HOST"],
        port=int(os.environ["NZ_DEV_PORT"]),
        database=os.environ["NZ_DEV_DATABASE"],
    )
    try:
        cur = conn.cursor()
        columns = ", ".join(f"{name} {kind}" for name, kind in COLUMNS)
        await cur.execute(f"CREATE TABLE {table} ({columns}) DISTRIBUTE ON RANDOM")
        rng = random.Random(args.seed)
        for _ in range(args.base_rows):
            await cur.execute(f"INSERT INTO {table} VALUES {_gen_row(rng)}")
        for _ in range(args.doublings):
            await cur.execute(f"INSERT INTO {table} SELECT * FROM {table}")
        await cur.execute(f"SELECT COUNT(*) FROM {table}")
        row_count = (await cur.fetchone())[0]
        result["row_count"] = row_count

        names = ", ".join(name for name, _ in COLUMNS)
        query = f"SELECT {names} FROM {table}"
        samples: dict[str, list[float]] = {"native": [], "python": []}
        for round_number in range(args.rounds):
            order = ("native", "python") if round_number % 2 == 0 else ("python", "native")
            for label in order:
                if label == "native":
                    _native._HAVE_NATIVE, _native._native_ext = True, native_module
                else:
                    _native._HAVE_NATIVE, _native._native_ext = False, None
                try:
                    reader = conn.cursor()
                    started = time.perf_counter()
                    await reader.execute(query)
                    rows = await reader.fetchall()
                    elapsed = time.perf_counter() - started
                finally:
                    _native._HAVE_NATIVE, _native._native_ext = original
                if len(rows) != row_count:
                    raise RuntimeError(f"{label} returned {len(rows)} rows; expected {row_count}")
                del rows
                samples[label].append(elapsed)
                print(f"round {round_number + 1}/{args.rounds} {label}: {elapsed:.3f}s", file=sys.stderr, flush=True)
    finally:
        _native._HAVE_NATIVE, _native._native_ext = original
        try:
            await conn.cursor().execute(f"DROP TABLE {table}")
            result["dropped"] = True
        except Exception as exc:  # noqa: BLE001 - reported in the result, not hidden
            result["dropped"] = f"FAILED: {exc}"
        await conn.close()

    native = _summary(samples["native"])
    python = _summary(samples["python"])
    result["native"] = native
    result["python"] = python
    result["python_over_native_median"] = round(python["median_s"] / native["median_s"], 3)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-rows", type=int, default=300, help="rows generated with INSERT (default 300)")
    parser.add_argument("--doublings", type=int, default=9, help="table doublings; 9 gives 300 * 2**9 rows")
    parser.add_argument("--rounds", type=int, default=5, help="interleaved native/python rounds")
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--output", type=Path, help="also write the JSON report here")
    args = parser.parse_args()

    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"missing required environment variables: {', '.join(missing)}")
    if args.base_rows <= 0 or args.doublings < 0 or args.rounds <= 0:
        raise SystemExit("--base-rows and --rounds must be positive; --doublings must not be negative")

    report = asyncio.run(_benchmark(args))
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if report.get("dropped") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
