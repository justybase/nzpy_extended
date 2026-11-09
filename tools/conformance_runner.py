#!/usr/bin/env python3
"""Conformance runner for nzpy_extended (Python).

Implements conformance/runner-contract.md v1. It is deliberately a plain
script: no pytest, no fixtures, no plugin state. The orchestrator executes it
as a subprocess.

    py -3.12 nzpy_extended/tools/conformance_runner.py --profile smoke

Exit codes: 0 ok, 1 failures, 2 configuration error, 3 crash.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(WORKSPACE_ROOT / "conformance" / "orchestrator"))

import canonical  # noqa: E402


def load_env(path: Path | None) -> None:
    if path is None:
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def config() -> dict:
    missing = [name for name in ("NZ_DEV_HOST", "NZ_DEV_USER", "NZ_DEV_PASSWORD") if not env(name)]
    database = env("NZ_DEV_DB") or env("NZ_DEV_DATABASE")
    if not database:
        missing.append("NZ_DEV_DB (or NZ_DEV_DATABASE)")
    if missing:
        raise SystemExit("python runner: missing configuration: " + ", ".join(missing))
    host = env("NZ_DEV_HOST")
    if host.lower() in {"localhost", "::1"}:
        host = "127.0.0.1"
    return {
        "host": host,
        "port": int(env("NZ_DEV_PORT", "5480")),
        "database": database,
        "user": env("NZ_DEV_USER"),
        "password": env("NZ_DEV_PASSWORD"),
        "schema": env("NZ_CONFORMANCE_SCHEMA", "CONFORMANCE"),
    }


def load_cases(cases_dir: Path) -> list[dict]:
    cases: list[dict] = []
    seen: set[str] = set()
    for path in sorted(cases_dir.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        version = document.get("kit_version")
        if version != 1:
            raise SystemExit(f"python runner: unsupported kit_version {version!r} in {path.name}")
        for case in document.get("cases", []):
            case_id = case.get("id")
            if not case_id:
                raise SystemExit(f"python runner: case without id in {path.name}")
            if case_id in seen:
                raise SystemExit(f"python runner: duplicate case id {case_id!r}")
            seen.add(case_id)
            cases.append(case)
    return cases


def select_cases(cases: list[dict], profile: str, filters: list[str]) -> list[dict]:
    selected = []
    for case in cases:
        if filters and not any(needle in case["id"] for needle in filters):
            continue
        if not filters:
            case_profile = case.get("profile", "full")
            if profile == "full":
                include = case_profile in {"full", "smoke"}
            else:
                include = case_profile == profile
            if not include:
                continue
        selected.append(case)
    return selected


def render_sql(sql: str, schema: str) -> str:
    if schema and schema.upper() != "CONFORMANCE":
        return sql.replace("CONFORMANCE.", f"{schema.upper()}.").replace(
            "'CONFORMANCE'", f"'{schema.upper()}'"
        )
    return sql


def refine_class(base: str, value) -> str:
    if base in {"timestamp", "timestamptz"} and isinstance(value, dt.datetime):
        return "timestamptz" if value.tzinfo is not None else "timestamp"
    if base in {"time", "timetz"} and isinstance(value, dt.time):
        return "timetz" if value.tzinfo is not None else "time"
    return base


def canonical_value(value, type_class: str):
    if value is None:
        return None
    if type_class in canonical.INTEGER_CLASSES:
        return str(int(value))
    if type_class in canonical.FLOAT_CLASSES:
        value = float(value)
        if value != value:
            return "NaN"
        if value == float("inf"):
            return "Infinity"
        if value == float("-inf"):
            return "-Infinity"
        return value
    if type_class == "numeric":
        return format(value, "f") if isinstance(value, Decimal) else str(value)
    if type_class == "bool":
        return bool(value)
    if type_class == "date":
        return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else str(value)
    if type_class in {"time", "timetz"}:
        text = value.isoformat() if isinstance(value, dt.time) else str(value)
        if type_class == "timetz":
            text = re.sub(r"([+-]\d{2})$", r"\1:00", text)
        return text
    if type_class in {"timestamp", "timestamptz"}:
        return value.isoformat(sep=" ") if isinstance(value, dt.datetime) else str(value)
    if type_class == "interval":
        return canonical.canonical_interval(
            getattr(value, "months", 0), getattr(value, "days", 0), getattr(value, "microseconds", 0)
        )
    if type_class == "bytes":
        if isinstance(value, (bytes, bytearray, memoryview)):
            return canonical.base64_bytes(value)
        return str(value)
    return str(value)


def columns_from_description(description, rows) -> list[dict]:
    if not description:
        return []
    first_values = []
    for index in range(len(description)):
        value = None
        for row in rows:
            if index < len(row) and row[index] is not None:
                value = row[index]
                break
        first_values.append(value)

    columns = []
    for index, entry in enumerate(description):
        name = entry[0]
        oid = entry[1]
        precision = entry[4] if len(entry) > 4 else None
        scale = entry[5] if len(entry) > 5 else None
        base = canonical.classify_oid(oid)
        type_class = refine_class(base, first_values[index])
        columns.append(
            {
                "name": name,
                "type_name": canonical.type_name_for_oid(oid, precision, scale),
                "type_class": type_class,
            }
        )
    return columns


def canonical_rows(rows, columns: list[dict]) -> list[list]:
    return [
        [
            canonical_value(value, columns[index]["type_class"] if index < len(columns) else "unknown")
            for index, value in enumerate(row)
        ]
        for row in rows
    ]


def classify_error(error: Exception) -> dict:
    name = type(error).__name__
    message = str(error)
    lowered = message.lower()
    if any(word in lowered for word in ("handshake", "ssl", "tls", "certificate", "self-signed")):
        kind = "connection"
    elif "timeout" in lowered or "timed out" in lowered or "cancelled" in lowered:
        kind = "timeout" if "timeout" in lowered or "timed out" in lowered else "cancelled"
    elif name == "ProgrammingError":
        if "syntax" in lowered or "expected next item" in lowered or "found \"" in lowered:
            kind = "syntax"
        elif "permission" in lowered or "denied" in lowered or "not authorized" in lowered:
            kind = "permission"
        elif "attribute" in lowered or "column" in lowered:
            kind = "column"
        elif "function" in lowered and ("does not exist" in lowered or "not exist" in lowered):
            kind = "function"
        elif "does not exist" in lowered or "not found" in lowered or "cannot find" in lowered:
            kind = "relation"
        elif "divide by zero" in lowered or "divide by 0" in lowered or "out of range" in lowered or "invalid" in lowered:
            kind = "type"
        else:
            kind = "other"
    elif name == "DataError":
        kind = "type"
    elif name == "IntegrityError":
        kind = "constraint"
    elif name in {"OperationalError", "InterfaceError", "ConnectionClosedError"}:
        kind = "connection"
    elif name == "NotSupportedError":
        kind = "other"
    else:
        kind = "other"
    code = getattr(error, "sqlstate", None)
    return {"class": kind, "message": message, "code": code}


def param_style(sql: str) -> str:
    if "%s" in sql:
        return "format"
    if "?" in sql:
        return "qmark"
    if ":" in sql:
        return "named"
    return "qmark"


def api_columns(spec: list[tuple[str, str, str]]) -> list[dict]:
    return [
        {"name": name, "type_name": type_name, "type_class": type_class}
        for name, type_name, type_class in spec
    ]


def normalized(text: str) -> str:
    return " ".join(str(text).upper().split())


async def api_metadata_ddl(connection, nz, settings: dict, spec: dict) -> tuple[list[dict], list[list]]:
    meta = connection.meta
    schema = spec.get("schema") or settings["schema"]
    table = spec["table"]
    checks: list[list] = []

    tables = await meta.get_tables(schema=schema)
    existing = {str(item.get("table_name", "")).upper() for item in tables}
    for name in spec.get("tables", []):
        checks.append(["tables", name, str(name).upper() in existing])

    columns = await meta.get_columns(table, schema=schema)
    names = {str(item.get("column_name", "")).upper() for item in columns}
    for name in spec.get("columns", []):
        checks.append(["columns", name, str(name).upper() in names])

    ddl = await meta.get_table_ddl(table, schema=schema)
    haystack = normalized(ddl)
    for fragment in spec.get("fragments", []):
        checks.append(["ddl", fragment, normalized(fragment) in haystack])

    return api_columns(
        [("check", "VARCHAR", "text"), ("name", "VARCHAR", "text"), ("ok", "BOOLEAN", "bool")]
    ), checks


async def api_pool_reuse(connection, nz, settings: dict, spec: dict) -> tuple[list[dict], list[list]]:
    pool = nz.NzPool(
        min_size=0,
        max_size=max(1, int(spec.get("max_size", 1))),
        idle_timeout=60,
        max_lifetime=3600,
        acquire_timeout=15,
        host=settings["host"],
        port=settings["port"],
        database=settings["database"],
        user=settings["user"],
        password=settings["password"],
    )
    checks: list[list] = []
    try:
        await pool.open()
        first = await pool.acquire()
        try:
            cursor = first.cursor()
            await cursor.execute("SELECT CURRENT_SID AS value")
            first_sid = (await cursor.fetchall())[0][0]
            await cursor.close()
        finally:
            await pool.release(first)
        second = await pool.acquire()
        try:
            cursor = second.cursor()
            await cursor.execute("SELECT CURRENT_SID AS value")
            second_sid = (await cursor.fetchall())[0][0]
            await cursor.close()
        finally:
            await pool.release(second)
        stats = pool.get_stats()
        checks.append(["same_session", first_sid == second_sid])
        checks.append(["stats_within_limit", int(stats.get("pool_size", 0)) <= int(pool.max_size)])
    finally:
        await pool.close_all()
    return api_columns(
        [("check", "VARCHAR", "text"), ("ok", "BOOLEAN", "bool")]
    ), checks


async def api_external_roundtrip(connection, nz, settings: dict, spec: dict) -> tuple[list[dict], list[list]]:
    schema = str(spec.get("schema") or settings["schema"]).upper()
    source = f"{schema}.CASE_EXT_SRC"
    destination = f"{schema}.CASE_EXT_DST"
    directory = tempfile.mkdtemp(prefix="nz-conformance-external-")
    sql_directory = directory.replace("\\", "/")
    external_file = f"{sql_directory}/export.txt"
    cursor = connection.cursor()
    try:
        statements = [
            f"DROP TABLE {source} IF EXISTS",
            f"DROP TABLE {destination} IF EXISTS",
            f"CREATE TABLE {source} (ID INTEGER, LABEL VARCHAR(20)) DISTRIBUTE ON (ID)",
            f"INSERT INTO {source} VALUES (1, 'one')",
            f"INSERT INTO {source} VALUES (2, 'two')",
            (
                f"CREATE EXTERNAL TABLE '{external_file}' USING "
                f"(REMOTESOURCE 'python' DELIMITER '|' LOGDIR '{sql_directory}') "
                f"AS SELECT * FROM {source}"
            ),
            f"CREATE TABLE {destination} (ID INTEGER, LABEL VARCHAR(20)) DISTRIBUTE ON (ID)",
            (
                f"INSERT INTO {destination} SELECT * FROM EXTERNAL '{external_file}' USING "
                f"(REMOTESOURCE 'python' DELIMITER '|' LOGDIR '{sql_directory}')"
            ),
        ]
        for statement in statements:
            await cursor.execute(statement)
            await cursor.fetchall()
        await cursor.execute(
            f"SELECT COUNT(*)::INTEGER AS row_count, COALESCE(SUM(ID), 0)::INTEGER AS checksum "
            f"FROM {destination}"
        )
        rows = await cursor.fetchall()
        return api_columns(
            [("row_count", "INTEGER", "int4"), ("checksum", "INTEGER", "int4")]
        ), [[str(rows[0][0]), str(rows[0][1])]]
    finally:
        try:
            for statement in (f"DROP TABLE {source} IF EXISTS", f"DROP TABLE {destination} IF EXISTS"):
                await cursor.execute(statement)
                await cursor.fetchall()
        finally:
            await cursor.close()
            shutil.rmtree(directory, ignore_errors=True)


async def api_tls_fail_closed(connection, nz, settings: dict, spec: dict) -> tuple[list[dict], list[list]]:
    tls_connection = await nz.connect(
        host=settings["host"],
        port=settings["port"],
        database=settings["database"],
        user=settings["user"],
        password=settings["password"],
        securityLevel=3,
        ssl_verify=True,
        connect_timeout=10,
    )
    await tls_connection.close()
    return api_columns([("check", "VARCHAR", "text")]), [["unexpected_tls_success"]]


API_OPERATIONS = {
    "metadata_ddl": api_metadata_ddl,
    "pool_reuse": api_pool_reuse,
    "external_roundtrip": api_external_roundtrip,
    "tls_fail_closed": api_tls_fail_closed,
}


async def execute_api_case(connection, nz, case: dict, settings: dict) -> dict:
    started = time.perf_counter()
    result: dict = {
        "case_id": case["id"],
        "status": "pass",
        "duration_ms": 0,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "result_sets": [],
        "error": None,
        "profile": case.get("profile", "full"),
    }
    operation = (case.get("api") or {}).get("operation")
    handler = API_OPERATIONS.get(operation)
    setup_cursor = connection.cursor()
    try:
        for statement in case.get("setup") or []:
            try:
                await setup_cursor.execute(render_sql(statement, settings["schema"]))
                await setup_cursor.fetchall()
            except Exception as setup_error:  # noqa: BLE001
                if "does not exist" in str(setup_error).lower():
                    continue
                raise
        if handler is None:
            raise ValueError(f"unsupported api operation {operation!r}")
        columns, rows = await handler(connection, nz, settings, case.get("api") or {})
        result["columns"] = columns
        result["rows"] = rows
        result["row_count"] = len(rows)
        result["result_sets"] = [
            {"columns": columns, "rows": rows, "row_count": len(rows)}
        ]
    except Exception as error:  # noqa: BLE001 - the runner reports the class
        result["status"] = "error"
        result["error"] = classify_error(error)
    finally:
        try:
            for statement in case.get("cleanup") or []:
                try:
                    await setup_cursor.execute(render_sql(statement, settings["schema"]))
                    await setup_cursor.fetchall()
                except Exception as cleanup_error:  # noqa: BLE001
                    print(f"cleanup warning [{case['id']}]: {cleanup_error}", file=sys.stderr)
        finally:
            await setup_cursor.close()
            result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


async def execute_case(connection, nz, case: dict, schema: str) -> dict:
    started = time.perf_counter()
    case_id = case["id"]
    sql = render_sql(case.get("sql", ""), schema)
    overrides = case.get("sql_overrides") or {}
    params = case.get("params") or []
    if params:
        sql = render_sql(overrides.get("python", sql), schema)
        if "$" in sql and "%s" not in sql and "?" not in sql:
            return {
                "case_id": case_id,
                "status": "error",
                "duration_ms": 0,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "error": {
                    "class": "configuration",
                    "message": "case uses $n placeholders but has no sql_overrides.python",
                    "code": None,
                },
            }
        nz.paramstyle = param_style(sql)

    cursor = connection.cursor()
    result: dict = {
        "case_id": case_id,
        "status": "pass",
        "duration_ms": 0,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "result_sets": [],
        "error": None,
        "profile": case.get("profile", "full"),
    }
    try:
        try:
            for statement in case.get("setup") or []:
                try:
                    await cursor.execute(render_sql(statement, schema), timeout=case.get("setup_timeout"))
                except Exception as setup_error:  # noqa: BLE001
                    if "does not exist" in str(setup_error).lower():
                        continue
                    raise
        except Exception as error:  # noqa: BLE001 - setup failure is a case error
            result["status"] = "error"
            result["error"] = classify_error(error)
            result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            await cursor.close()
            return result

        result_sets = []
        if case.get("multi_statement"):
            statements = [part.strip() for part in sql.split(";") if part.strip()]
            for statement in statements:
                await cursor.execute(statement, timeout=case.get("timeout"))
                set_rows = await cursor.fetchall()
                if cursor.description:
                    result_sets.append((cursor.description, set_rows))
        else:
            await cursor.execute(
                sql,
                [item.get("value") for item in params] if params else None,
                timeout=case.get("timeout"),
            )
            description = cursor.description
            rows = await cursor.fetchall()
            if description:
                result_sets.append((description, rows))

        primary_description, primary_rows = result_sets[0] if result_sets else (None, [])
        columns = columns_from_description(primary_description, primary_rows)
        result["columns"] = columns
        result["rows"] = canonical_rows(primary_rows, columns)
        result["row_count"] = len(primary_rows)
        result["result_sets"] = []
        for desc, set_rows in result_sets:
            set_columns = columns_from_description(desc, set_rows)
            result["result_sets"].append(
                {
                    "columns": set_columns,
                    "rows": canonical_rows(set_rows, set_columns),
                    "row_count": len(set_rows),
                }
            )
    except Exception as error:  # noqa: BLE001 - the runner reports the class
        result["status"] = "error"
        result["error"] = classify_error(error)
        try:
            await connection.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            for statement in case.get("cleanup") or []:
                try:
                    await cursor.execute(render_sql(statement, schema))
                except Exception as cleanup_error:  # noqa: BLE001
                    print(f"cleanup warning [{case_id}]: {cleanup_error}", file=sys.stderr)
        finally:
            await cursor.close()
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


async def run(args) -> int:
    load_env(Path(args.env) if args.env else None)
    settings = config()
    cases_dir = Path(args.cases_dir) if args.cases_dir else WORKSPACE_ROOT / "conformance" / "cases"
    cases = select_cases(load_cases(cases_dir), args.profile, args.case)
    if not cases:
        print("python runner: no cases selected", file=sys.stderr)
        return 2

    import nzpy_extended as nz

    try:
        connection = await nz.connect(
            host=settings["host"],
            port=settings["port"],
            database=settings["database"],
            user=settings["user"],
            password=settings["password"],
        )
    except Exception as error:  # noqa: BLE001
        print(f"python runner: connection failed: {error}", file=sys.stderr)
        return 2

    out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    failures = 0
    try:
        for case in cases:
            if case.get("api"):
                result = await execute_api_case(connection, nz, case, settings)
            else:
                result = await execute_case(connection, nz, case, settings["schema"])
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
            out.flush()
            marker = "ok  " if result["status"] == "pass" else "ERR "
            print(f"{marker} {case['id']} ({result['duration_ms']} ms)", file=sys.stderr)
            if result["status"] == "error" and args.fail_fast:
                failures += 1
                break
    finally:
        if args.out:
            out.close()
        try:
            await connection.close()
        except Exception:  # noqa: BLE001
            pass
    return 0 if failures == 0 else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--case", action="append", default=[], help="substring filter, repeatable")
    parser.add_argument("--cases-dir")
    parser.add_argument("--out")
    parser.add_argument("--env")
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001
        print(f"python runner crash: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
