#!/usr/bin/env python3
"""Create and remove an isolated Netezza object for the browser suite."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import nzpy_extended.sync as nzpy


def connection():
    return nzpy.connect(
        user=os.environ.get("NZ_DEV_USER", "admin"),
        password=os.environ.get("NZ_DEV_PASSWORD", "password"),
        host=os.environ.get("NZ_DEV_HOST", "192.168.0.144"),
        port=int(os.environ.get("NZ_DEV_PORT", "5480")),
        database=os.environ.get("NZ_DEV_DATABASE") or os.environ.get("NZ_DEV_DB", "JUST_DATA"),
    )


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "create"
    if action == "create":
        table = f"NZPY_E2E_{secrets.token_hex(5).upper()}"
        conn = connection()
        try:
            conn.execute(f"CREATE TABLE {table} (ID INTEGER, LABEL VARCHAR(80), AMOUNT NUMERIC(12, 2))")
            for row in (
                "(1, 'alpha', 10.50)",
                "(2, 'beta', 20.25)",
                "(3, 'gamma', 30.75)",
            ):
                conn.execute(f"INSERT INTO {table} VALUES {row}")
            conn.commit()
        finally:
            conn.close()
        print(table)
        return
    if action == "drop" and len(sys.argv) == 3:
        table = sys.argv[2]
        if not table.startswith("NZPY_E2E_"):
            raise SystemExit("refusing to drop a non-fixture table")
        conn = connection()
        try:
            conn.execute(f"DROP TABLE {table}")
            conn.commit()
        finally:
            conn.close()
        return
    raise SystemExit("usage: fixture.py create|drop TABLE")


if __name__ == "__main__":
    main()
