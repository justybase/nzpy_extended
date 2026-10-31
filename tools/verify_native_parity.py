#!/usr/bin/env python3
"""Run the DBOS decoder parity tests in both modes.

Two modes are exercised:

* native (default): ``nzpy_extended.rust_ext`` is used.
* pure Python: ``NZPY_EXTENDED_NO_NATIVE=1`` forces the pure-Python decoder.

Extra arguments are passed through to pytest.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TEST_FILES = [
    "tests/test_dbos_malformed_unit.py",
    "tests/test_dbos_decoder_unit.py",
    "tests/test_wire_fuzz_unit.py",
    "tests/test_regressions_unit.py",
    "tests/test_native_parity_unit.py",
]

MODES: tuple[tuple[str, dict[str, str]], ...] = (
    ("native (rust_ext)", {}),
    ("pure Python", {"NZPY_EXTENDED_NO_NATIVE": "1"}),
)


def _run(env: dict[str, str], extra_args: list[str]) -> int:
    cmd = [sys.executable, "-m", "pytest", *TEST_FILES, "-v", "--tb=short", *extra_args]
    return subprocess.call(cmd, cwd=ROOT, env=env)


def main() -> int:
    extra_args = sys.argv[1:]
    failures = 0
    for label, overrides in MODES:
        env = os.environ.copy()
        env.pop("NZPY_EXTENDED_NO_NATIVE", None)
        env.update(overrides)
        print(f"== {label} ==", flush=True)
        failures += _run(env, extra_args) != 0
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
