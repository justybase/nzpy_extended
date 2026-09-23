"""Interleaved live-DB A/B benchmark for DBOS result decoding.

The parent process runs each baseline/candidate sample in a fresh subprocess
and alternates driver order. This avoids comparing two imported versions in
one interpreter and excludes process startup and connection setup from the
query-and-fetch timing.

Example (both source roots must have a built C extension)::

    python tools/examples/benchmark_dbos_ab.py \
        --baseline-root /tmp/nzpy-baseline \
        --candidate-root "$PWD" \
        --source-table BENCH_ROWS \
        --output /tmp/dbos-ab.json

Connection settings must be supplied with NZ_DEV_HOST, NZ_DEV_PORT,
NZ_DEV_DATABASE, NZ_DEV_USER, and NZ_DEV_PASSWORD. No credential defaults
are used. Run only against a disposable Netezza database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - resource is unavailable on Windows.
    resource = None  # type: ignore[assignment]


SCENARIOS: dict[str, str] = {
    "integer_types": """
        SELECT (RANDOM()*10000)::INT, (RANDOM()*10000)::BIGINT,
               (RANDOM()*100)::SMALLINT, (RANDOM()*10)::BYTEINT
        FROM {source} LIMIT {rows}
    """,
    "numeric_types": """
        SELECT (RANDOM()*10000)::NUMERIC(20,4),
               (RANDOM()*10000)::DECIMAL(18,2), (RANDOM()*10000)::REAL,
               (RANDOM()*10000)::DOUBLE PRECISION
        FROM {source} LIMIT {rows}
    """,
    "string_types": """
        SELECT (RANDOM()*10000)::VARCHAR(50),
               (RANDOM()*10000)::NVARCHAR(50), (RANDOM()*10000)::CHAR(20)
        FROM {source} LIMIT {rows}
    """,
    "datetime_types": """
        SELECT CURRENT_DATE + (RANDOM()*365)::INT, CURRENT_TIME,
               CURRENT_TIMESTAMP
        FROM {source} LIMIT {rows}
    """,
    "boolean_types": """
        SELECT CASE WHEN RANDOM() > 0.5 THEN TRUE ELSE FALSE END,
               CASE WHEN RANDOM() > 0.5 THEN TRUE ELSE FALSE END
        FROM {source} LIMIT {rows}
    """,
    "all_types": """
        SELECT (RANDOM()*10000)::INT, (RANDOM()*10000)::BIGINT,
               (RANDOM()*100)::SMALLINT, (RANDOM()*10)::BYTEINT,
               (RANDOM()*10000)::NUMERIC(20,4),
               (RANDOM()*10000)::DECIMAL(18,2), (RANDOM()*10000)::REAL,
               (RANDOM()*10000)::DOUBLE PRECISION,
               (RANDOM()*10000)::VARCHAR(50),
               (RANDOM()*10000)::NVARCHAR(50), (RANDOM()*10000)::CHAR(20),
               CURRENT_DATE + (RANDOM()*365)::INT, CURRENT_TIME,
               CURRENT_TIMESTAMP,
               CASE WHEN RANDOM() > 0.5 THEN TRUE ELSE FALSE END
        FROM {source} LIMIT {rows}
    """,
}

MODES = ("fetchall", "fetchmany")
REQUIRED_ENV = (
    "NZ_DEV_HOST",
    "NZ_DEV_PORT",
    "NZ_DEV_DATABASE",
    "NZ_DEV_USER",
    "NZ_DEV_PASSWORD",
)


def _query(scenario: str, source_table: str, rows: int) -> str:
    return SCENARIOS[scenario].format(source=source_table, rows=rows)


async def _worker(args: argparse.Namespace) -> dict[str, Any]:
    source_root = Path(args.root).resolve()
    if not (source_root / "nzpy_extended" / "__init__.py").is_file():
        raise RuntimeError(f"driver source root not found: {source_root}")
    sys.path.insert(0, str(source_root))
    if args.disable_cext:
        os.environ["NZPY_EXTENDED_NO_CEXT"] = "1"

    import nzpy_extended as nzpy
    from nzpy_extended import _cstate

    if _cstate._HAVE_C_EXT == args.disable_cext:
        expected = "disabled" if args.disable_cext else "enabled"
        raise RuntimeError(f"C extension should be {expected} in {source_root}")

    connect_start = time.perf_counter()
    conn = await nzpy.connect(
        user=os.environ["NZ_DEV_USER"],
        password=os.environ["NZ_DEV_PASSWORD"],
        host=os.environ["NZ_DEV_HOST"],
        port=int(os.environ.get("NZ_DEV_PORT", "5480")),
        database=os.environ["NZ_DEV_DATABASE"],
    )
    connect_seconds = time.perf_counter() - connect_start

    cur = conn.cursor()
    query_start = time.perf_counter()
    try:
        await cur.execute(_query(args.scenario, args.source_table, args.rows))
        row_count = 0
        if args.mode == "fetchall":
            row_count = len(await cur.fetchall())
        else:
            while True:
                batch = await cur.fetchmany(1000)
                if not batch:
                    break
                row_count += len(batch)
        query_seconds = time.perf_counter() - query_start
    finally:
        await cur.close()
        await conn.close()

    return {
        "scenario": args.scenario,
        "mode": args.mode,
        "rows": row_count,
        "connect_seconds": connect_seconds,
        "query_seconds": query_seconds,
        "rows_per_second": row_count / query_seconds if query_seconds else 0.0,
        "peak_rss_mb": _peak_rss_mb(),
        "cext_enabled": _cstate._HAVE_C_EXT,
    }


def _peak_rss_mb() -> float | None:
    if resource is None:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux and the other supported Unix targets report KiB.
    return peak / (1024 * 1024 if sys.platform == "darwin" else 1024)


def _run_sample(
    root: Path,
    scenario: str,
    mode: str,
    source_table: str,
    rows: int,
    disable_cext: bool,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--root",
        str(root),
        "--scenario",
        scenario,
        "--mode",
        mode,
        "--source-table",
        source_table,
        "--rows",
        str(rows),
    ]
    if disable_cext:
        command.append("--disable-cext")
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(
            f"benchmark worker failed ({root}, {scenario}, {mode}):\n"
            f"{completed.stderr.strip()}"
        )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _main(args: argparse.Namespace) -> int:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"missing required environment variables: {', '.join(missing)}")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.$]*", args.source_table):
        raise SystemExit("--source-table must be a plain Netezza table identifier")
    if args.rows <= 0 or args.rounds <= 0:
        raise SystemExit("--rows and --rounds must be positive")

    roots = {"baseline": Path(args.baseline_root), "candidate": Path(args.candidate_root)}
    results: list[dict[str, Any]] = []
    scenario_names = [args.scenario] if args.scenario else list(SCENARIOS)
    modes = [args.mode] if args.mode else list(MODES)

    for scenario in scenario_names:
        for mode in modes:
            for round_number in range(args.rounds):
                order = ("baseline", "candidate") if round_number % 2 == 0 else (
                    "candidate",
                    "baseline",
                )
                pair: dict[str, dict[str, Any]] = {}
                for label in order:
                    sample = _run_sample(
                        roots[label],
                        scenario,
                        mode,
                        args.source_table,
                        args.rows,
                        args.disable_cext,
                    )
                    if sample["rows"] != args.rows:
                        raise RuntimeError(
                            f"{label}/{scenario}/{mode} returned {sample['rows']} "
                            f"rows; expected {args.rows}"
                        )
                    sample["variant"] = label
                    sample["round"] = round_number + 1
                    pair[label] = sample
                    results.append(sample)
                print(
                    f"{scenario}/{mode} pair={round_number + 1}/{args.rounds} "
                    f"baseline={pair['baseline']['rows_per_second']:.0f} rows/s "
                    f"candidate={pair['candidate']['rows_per_second']:.0f} rows/s",
                    flush=True,
                )

    summary: list[dict[str, Any]] = []
    for scenario in scenario_names:
        for mode in modes:
            selected = [
                item
                for item in results
                if item["scenario"] == scenario and item["mode"] == mode
            ]
            baseline = statistics.median(
                item["rows_per_second"] for item in selected if item["variant"] == "baseline"
            )
            candidate = statistics.median(
                item["rows_per_second"] for item in selected if item["variant"] == "candidate"
            )
            summary.append(
                {
                    "scenario": scenario,
                    "mode": mode,
                    "baseline_median_rows_per_second": baseline,
                    "candidate_median_rows_per_second": candidate,
                    "median_change_percent": (candidate / baseline - 1.0) * 100.0,
                }
            )

    report = {
        "database": os.environ["NZ_DEV_DATABASE"],
        "source_table": args.source_table,
        "rows_per_query": args.rows,
        "rounds": args.rounds,
        "summary": summary,
        "samples": results,
    }
    report_path = Path(args.output)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {report_path}")
    for item in summary:
        print(
            f"{item['scenario']}/{item['mode']}: "
            f"{item['median_change_percent']:+.2f}% median"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--source-table")
    parser.add_argument("--output", type=Path, default=Path("/tmp/nzpy-dbos-ab.json"))
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--scenario", choices=tuple(SCENARIOS))
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--disable-cext", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker:
        print(json.dumps(asyncio.run(_worker(args))))
        return 0
    if not all((args.baseline_root, args.candidate_root, args.source_table)):
        parser.error("--baseline-root, --candidate-root, and --source-table are required")
    return _main(args)


if __name__ == "__main__":
    raise SystemExit(main())
