"""Independent JustyBase driver used for differential integration tests.

The bridge talks JSON-lines to a separate Node process. This keeps the
reference protocol implementation independent from nzpy_extended without
requiring an ODBC installation for parity checks.
"""

from __future__ import annotations

import datetime
import decimal
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


_ROOT = Path(__file__).resolve().parent
_BRIDGE = _ROOT / "reference_driver.js"
_DEFAULT_DRIVER = _ROOT.parent.parent / "justybase_netezza_node_driver"
_TEXT_OIDS = frozenset({19, 25, 1042, 1043, 2522, 2530})
_TEXT_VECTOR_OIDS = frozenset({22, 30})
_EMPTY_VALUE_OIDS = _TEXT_OIDS | _TEXT_VECTOR_OIDS


@dataclass(frozen=True)
class _ReferenceUndefined:
    oid: int


def _driver_root() -> Path:
    configured = os.environ.get("NZ_REFERENCE_NODE_DRIVER")
    return Path(configured).expanduser() if configured else _DEFAULT_DRIVER


def _require_reference_driver() -> tuple[str, Path]:
    node = shutil.which("node")
    driver_root = _driver_root()
    if node is None:
        pytest.skip("Reference driver requires Node.js")
    if not (driver_root / "dist" / "cjs" / "index.js").is_file():
        pytest.skip(
            "Reference driver is unavailable; set NZ_REFERENCE_NODE_DRIVER "
            "to a built @justybase/netezza-driver checkout"
        )
    return node, driver_root


def _decode_value(value: dict[str, Any], oid: int) -> Any:
    kind = value["kind"]
    raw = value.get("value")
    if kind == "null":
        return None
    if kind == "undefined":
        return _ReferenceUndefined(oid)
    if kind == "bool":
        return bool(raw)
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "numeric":
        return decimal.Decimal(raw)
    if kind == "date":
        return datetime.date.fromisoformat(raw)
    if kind == "datetime":
        return datetime.datetime.fromisoformat(raw)
    if kind == "time":
        return datetime.time.fromisoformat(raw)
    if kind == "bytes":
        import base64
        return base64.b64decode(raw)
    return raw


class ReferenceCursor:
    def __init__(self, connection: "ReferenceConnection") -> None:
        self._connection = connection
        self._rows: list[list[Any]] = []
        self._fields: list[dict[str, Any]] = []

    def execute(self, sql: str) -> "ReferenceCursor":
        result = self._connection.query(sql)
        self._fields = result["fields"]
        self._rows = [
            [
                _decode_value(value, self._fields[index]["oid"])
                for index, value in enumerate(row)
            ]
            for row in result["rows"]
        ]
        return self

    def fetchall(self) -> list[list[Any]]:
        return self._rows

    def fetchone(self) -> list[Any] | None:
        return self._rows[0] if self._rows else None

    @property
    def description(self) -> list[tuple[Any, ...]]:
        return [(field["name"], field["oid"]) for field in self._fields]

    def close(self) -> None:
        return None


class ReferenceConnection:
    def __init__(self) -> None:
        node, driver_root = _require_reference_driver()
        env = os.environ.copy()
        env["NZ_REFERENCE_NODE_DRIVER"] = str(driver_root)
        self._process = subprocess.Popen(
            [node, str(_BRIDGE)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )

    def cursor(self) -> ReferenceCursor:
        return ReferenceCursor(self)

    def query(self, sql: str) -> dict[str, Any]:
        if self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("Reference driver process has no stdio")
        self._process.stdin.write(json.dumps({"sql": sql}) + "\n")
        self._process.stdin.flush()
        line = self._process.stdout.readline()
        if not line:
            error = self._process.stderr.read() if self._process.stderr is not None else ""
            raise RuntimeError(f"Reference driver stopped unexpectedly: {error}")
        result = json.loads(line)
        if not result.get("ok"):
            raise RuntimeError(f"Reference driver query failed: {result.get('error')}")
        return result

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            self._process.wait(timeout=5)


def compare_reference_rows(
    nz_rows: list[list[Any]],
    reference_rows: list[list[Any]],
    query: str,
    reference_description: list[tuple[Any, ...]] | None = None,
) -> None:
    """Compare values from nzpy_extended with the independent driver."""
    import math

    def temporal(value: Any) -> datetime.date | datetime.datetime | datetime.time | None:
        if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
            return value
        if not isinstance(value, str):
            return None
        text = value.strip().replace("T", " ")
        for parser in (datetime.datetime.fromisoformat, datetime.date.fromisoformat, datetime.time.fromisoformat):
            try:
                return parser(text)
            except ValueError:
                continue
        return None

    def equal_values(left: Any, right: Any, oid: int | None) -> bool:
        if isinstance(right, _ReferenceUndefined):
            return right.oid in _EMPTY_VALUE_OIDS and left == ""
        if oid in _EMPTY_VALUE_OIDS and (
            (left is None and right == "") or (left == "" and right is None)
        ):
            # The JustyBase Node API currently exposes zero-length textual
            # values as null. Restrict this compatibility rule to textual
            # and vector OIDs; true SQL NULL still compares as None on both
            # sides. OIDs 22/30 are int2vector/oidvector in these catalogs.
            return True
        if isinstance(left, bool) or isinstance(right, bool):
            return left == right and isinstance(left, bool) == isinstance(right, bool)
        if isinstance(left, (int, float, decimal.Decimal)) and isinstance(right, (int, float, decimal.Decimal)):
            return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-12)

        left_time = temporal(left)
        right_time = temporal(right)
        if left_time is not None and right_time is not None:
            if isinstance(left_time, datetime.datetime) and isinstance(right_time, datetime.datetime):
                return abs((left_time - right_time).total_seconds()) <= 1
            return left_time == right_time

        if isinstance(left, str) and isinstance(right, str):
            # Fixed-width CHAR/NCHAR values may be padded by one driver and
            # trimmed by the other.  Preserve meaningful leading/interior
            # whitespace while ignoring only the server-added suffix.
            return left == right or left.rstrip() == right.rstrip()
        if isinstance(left, (list, tuple)) and isinstance(right, str):
            return " ".join(str(item) for item in left) == right.strip()
        if oid == 17 and isinstance(left, bytes) and isinstance(right, str):
            try:
                return left.decode("utf-8") == right
            except UnicodeDecodeError:
                return False
        return left == right

    assert len(nz_rows) == len(reference_rows), f"Row count mismatch for {query!r}"
    for row_idx, (nz_row, ref_row) in enumerate(zip(nz_rows, reference_rows)):
        assert len(nz_row) == len(ref_row), f"Column count mismatch for {query!r}"
        for col_idx, (nz_value, ref_value) in enumerate(zip(nz_row, ref_row)):
            oid = (
                reference_description[col_idx][1]
                if reference_description is not None
                else None
            )
            equal = equal_values(nz_value, ref_value, oid)
            assert equal, (
                f"{query!r}: row {row_idx} col {col_idx}: "
                f"nzpy={nz_value!r} reference={ref_value!r}"
            )


def reference_rows(sql: str) -> list[list[Any]]:
    connection = ReferenceConnection()
    try:
        cursor = connection.cursor()
        cursor.execute(sql)
        return cursor.fetchall()
    finally:
        connection.close()
