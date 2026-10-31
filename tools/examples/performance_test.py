import argparse
import asyncio
import time
import os
import sys

# Automatically add the main directory to the path to avoid ModuleNotFoundError
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import nzpy_extended
from nzpy_extended import _native as nzpy_native

# Official nzpy is required for comparison
import nzpy

# pyodbc is optional
try:
    import pyodbc
    PYODBC_AVAILABLE = True
except ImportError:
    pyodbc = None
    PYODBC_AVAILABLE = False

# Connection settings come from NZ_DEV_* (the project's live-test variables) or the
# legacy NZ_* names. There are no host, user or password defaults; main() refuses
# to run when a setting is missing.
def _setting(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return ""


_SETTING_NAMES = {
    "host": ("NZ_DEV_HOST", "NZ_HOST"),
    "port": ("NZ_DEV_PORT", "NZ_PORT"),
    "user": ("NZ_DEV_USER", "NZ_USER"),
    "password": ("NZ_DEV_PASSWORD", "NZ_PASSWORD"),
    "database": ("NZ_DEV_DATABASE", "NZ_DATABASE"),
}
HOST = _setting(*_SETTING_NAMES["host"])
PORT = int(_setting(*_SETTING_NAMES["port"]) or "0")
USER = _setting(*_SETTING_NAMES["user"])
PASSWORD = _setting(*_SETTING_NAMES["password"])
DATABASE = _setting(*_SETTING_NAMES["database"])
ROW_LIMIT = int(os.getenv("NZ_ROWS", "100000"))

ODBC_DRIVER_NAME = os.getenv("NZ_ODBC_DRIVER", "NetezzaSQL")
SOURCE_TABLE = "JUST_DATA..FACTPRODUCTINVENTORY"

# Check if pyodbc can actually connect (may fail on Linux/WSL2 due to
# the Netezza ODBC driver's Unicode API limitation)
PYODBC_WORKING = PYODBC_AVAILABLE
PYODBC_HINT = ""
if PYODBC_AVAILABLE:
    try:
        _test_cs = (
            f"DRIVER={{{ODBC_DRIVER_NAME}}};SERVER={HOST};PORT={PORT};"
            f"DATABASE={DATABASE};UID={USER};PWD={PASSWORD};"
        )
        _c = pyodbc.connect(_test_cs, timeout=3, autocommit=True)  # type: ignore[union-attr]
        _c.close()
    except Exception:
        PYODBC_WORKING = False
        PYODBC_HINT = (
            "pyodbc native connect failed (Unicode API issue on Linux), "
            "falling back to ANSI-ODBC via ctypes"
        )

# ─── ANSI-ODBC fallback for Linux ──────────────────────────────────────────
# pyodbc uses SQLDriverConnectW (Unicode), which the Netezza ODBC driver
# does not support on Linux. This wrapper uses SQLDriverConnectA (ANSI)
# via ctypes, bypassing the Unicode limitation.

_ANSI_ODBC_AVAILABLE = False
_AnsiOdbcConnection = None  # placeholder

try:
    import ctypes

    _odbc = ctypes.CDLL("libodbc.so.2")

    _SQLAllocHandle = _odbc.SQLAllocHandle
    _SQLAllocHandle.argtypes = [ctypes.c_short, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _SQLAllocHandle.restype = ctypes.c_short

    _SQLSetEnvAttr = _odbc.SQLSetEnvAttr
    _SQLSetEnvAttr.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    _SQLSetEnvAttr.restype = ctypes.c_short

    _SQLDriverConnectA = _odbc.SQLDriverConnectA
    _SQLDriverConnectA.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_short,
        ctypes.c_char_p, ctypes.c_short, ctypes.POINTER(ctypes.c_short), ctypes.c_short,
    ]
    _SQLDriverConnectA.restype = ctypes.c_short

    _SQLExecDirectA = _odbc.SQLExecDirectA
    _SQLExecDirectA.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    _SQLExecDirectA.restype = ctypes.c_short

    _SQLFetch = _odbc.SQLFetch
    _SQLFetch.argtypes = [ctypes.c_void_p]
    _SQLFetch.restype = ctypes.c_short

    _SQLGetData = _odbc.SQLGetData
    _SQLGetData.argtypes = [
        ctypes.c_void_p, ctypes.c_short, ctypes.c_short,
        ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
    ]
    _SQLGetData.restype = ctypes.c_short

    _SQLNumResultCols = _odbc.SQLNumResultCols
    _SQLNumResultCols.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_short)]
    _SQLNumResultCols.restype = ctypes.c_short

    _SQLFreeHandle = _odbc.SQLFreeHandle
    _SQLFreeHandle.argtypes = [ctypes.c_short, ctypes.c_void_p]
    _SQLFreeHandle.restype = ctypes.c_short

    _SQLDisconnect = _odbc.SQLDisconnect
    _SQLDisconnect.argtypes = [ctypes.c_void_p]
    _SQLDisconnect.restype = ctypes.c_short

    class _AnsiOdbcCursor:
        def __init__(self, hdbc):
            self._hdbc = hdbc
            self._hstmt = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

        def close(self):
            if self._hstmt is not None:
                _SQLFreeHandle(ctypes.c_short(3), self._hstmt)
                self._hstmt = None

        def execute(self, query):
            self.close()
            self._hstmt = ctypes.c_voidp()
            _SQLAllocHandle(ctypes.c_short(3), self._hdbc, ctypes.byref(self._hstmt))
            qbytes = query.encode("utf-8")
            ret = _SQLExecDirectA(self._hstmt, qbytes, -3)
            if ret not in (0, 1):
                raise RuntimeError(f"SQLExecDirectA failed: ret={ret}")

        def fetchall(self):
            # NOTE: This fetches every cell as SQL_C_CHAR (string) via
            # individual SQLGetData calls — no SQLBindCol batch fetching.
            # This is significantly (~3-7×) slower than native pyodbc on
            # Windows, which uses C-level batch/bound-column fetching.
            rows = []
            ncols = ctypes.c_short()
            _SQLNumResultCols(self._hstmt, ctypes.byref(ncols))
            icol_max = ncols.value
            while True:
                ret = _SQLFetch(self._hstmt)
                if ret == 100:
                    break
                if ret not in (0, 1):
                    break
                cols = []
                for icol in range(1, icol_max + 1):
                    buf = ctypes.create_string_buffer(4096)
                    ind = ctypes.c_int(-1)
                    ret = _SQLGetData(
                        self._hstmt, ctypes.c_short(icol), ctypes.c_short(1),
                        buf, ctypes.c_int(ctypes.sizeof(buf)),
                        ctypes.byref(ind),
                    )
                    if ret in (0, 1):
                        if ind.value == -1:
                            cols.append(None)
                        else:
                            val = buf.raw[:ind.value].decode("utf-8", errors="replace")
                            cols.append(val)
                    elif ret == 100:
                        cols.append(None)
                    else:
                        cols.append(None)
                rows.append(tuple(cols))
            return rows

    class _AnsiOdbcConnection:
        def __init__(self, conn_str):
            self._henv = ctypes.c_voidp()
            self._hdbc = ctypes.c_voidp()
            _SQLAllocHandle(ctypes.c_short(1), 0, ctypes.byref(self._henv))
            _SQLSetEnvAttr(self._henv, 200, ctypes.c_void_p(3), 0)
            _SQLAllocHandle(ctypes.c_short(2), self._henv, ctypes.byref(self._hdbc))
            cbytes = conn_str.encode("utf-8")
            ret = _SQLDriverConnectA(
                self._hdbc, None, cbytes, ctypes.c_short(-3),
                None, ctypes.c_short(0), None, ctypes.c_short(0),
            )
            if ret not in (0, 1):
                raise RuntimeError(f"ANSI ODBC connect failed: ret={ret}")

        def cursor(self):
            return _AnsiOdbcCursor(self._hdbc)

        def close(self):
            if self._hdbc is not None:
                _SQLDisconnect(self._hdbc)
                _SQLFreeHandle(ctypes.c_short(2), self._hdbc)
                self._hdbc = None
            if self._henv is not None:
                _SQLFreeHandle(ctypes.c_short(1), self._henv)
                self._henv = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    _ANSI_ODBC_AVAILABLE = True

except Exception as _exc:
    pass

OUTPUT_LINES = []

# ─── Data-type-specific queries ───────────────────────────────────────────────

QUERIES = {
    "integer_types": f"""
        SELECT
            (RANDOM()*10000)::INT       AS col_int,
            (RANDOM()*10000)::BIGINT    AS col_bigint,
            (RANDOM()*100)::SMALLINT    AS col_smallint,
            (RANDOM()*10)::BYTEINT      AS col_byteint
        FROM {SOURCE_TABLE}
        LIMIT {ROW_LIMIT}
    """,
    "numeric_types": f"""
        SELECT
            (RANDOM()*10000)::NUMERIC(20,4)  AS col_numeric,
            (RANDOM()*10000)::DECIMAL(18,2)  AS col_decimal,
            (RANDOM()*10000)::REAL           AS col_real,
            (RANDOM()*10000)::DOUBLE PRECISION AS col_double
        FROM {SOURCE_TABLE}
        LIMIT {ROW_LIMIT}
    """,
    "string_types": f"""
        SELECT
            (RANDOM()*10000)::VARCHAR(50)  AS col_varchar,
            (RANDOM()*10000)::NVARCHAR(50) AS col_nvarchar,
            (RANDOM()*10000)::CHAR(20)     AS col_char
        FROM {SOURCE_TABLE}
        LIMIT {ROW_LIMIT}
    """,
    "datetime_types": f"""
        SELECT
            CURRENT_DATE + (RANDOM()*365)::INT    AS col_date,
            CURRENT_TIME                          AS col_time,
            CURRENT_TIMESTAMP                     AS col_timestamp
        FROM {SOURCE_TABLE}
        LIMIT {ROW_LIMIT}
    """,
    "boolean_types": f"""
        SELECT
            CASE WHEN RANDOM() > 0.5 THEN TRUE  ELSE FALSE END AS col_bool,
            CASE WHEN RANDOM() > 0.5 THEN TRUE  ELSE FALSE END AS col_boolean
        FROM {SOURCE_TABLE}
        LIMIT {ROW_LIMIT}
    """,
}

ALL_TYPES_QUERY = f"""
    SELECT
        (RANDOM()*10000)::INT              AS col_int,
        (RANDOM()*10000)::BIGINT           AS col_bigint,
        (RANDOM()*100)::SMALLINT           AS col_smallint,
        (RANDOM()*10)::BYTEINT             AS col_byteint,
        (RANDOM()*10000)::NUMERIC(20,4)    AS col_numeric,
        (RANDOM()*10000)::DECIMAL(18,2)    AS col_decimal,
        (RANDOM()*10000)::REAL             AS col_real,
        (RANDOM()*10000)::DOUBLE PRECISION AS col_double,
        (RANDOM()*10000)::VARCHAR(50)      AS col_varchar,
        (RANDOM()*10000)::NVARCHAR(50)     AS col_nvarchar,
        (RANDOM()*10000)::CHAR(20)         AS col_char,
        CURRENT_DATE + (RANDOM()*365)::INT AS col_date,
        CURRENT_TIME                       AS col_time,
        CURRENT_TIMESTAMP                  AS col_timestamp,
        CASE WHEN RANDOM() > 0.5 THEN TRUE ELSE FALSE END AS col_bool
    FROM {SOURCE_TABLE}
    LIMIT {ROW_LIMIT}
"""


QUERIES["all_types"] = ALL_TYPES_QUERY


DRIVER_NAMES = [
    "official_nzpy",
    "pyodbc",
    "nzpy_extended_async",
    "nzpy_extended_sync",
    "nzpy_extended_async_nonative",
    "nzpy_extended_sync_nonative",
]


def out(*args, **kwargs):
    """Print and capture output for TXT export."""
    end = kwargs.get("end", "\n")
    print(*args, **kwargs)
    line = " ".join(str(a) for a in args)
    if end == "\n":
        OUTPUT_LINES.append(line)
    else:
        if OUTPUT_LINES and not OUTPUT_LINES[-1].endswith(" "):
            OUTPUT_LINES[-1] += " " + line
        else:
            OUTPUT_LINES[-1] += line


def fmt_num(n):
    """Format an integer with a space as thousand separator."""
    return f"{n:,}".replace(",", " ")


def fmt_rps(rps):
    """Format rows/s value with a space as thousand separator."""
    return f"{rps:_.2f}".replace("_", " ")


async def _run_async_query(conn, label, query, results_dict):
    async with conn.cursor() as cursor:
        out(f"  {label}: Executing...", end=" ")
        start = time.perf_counter()
        await cursor.execute(query)
        rows = await cursor.fetchall()
        elapsed = time.perf_counter() - start
        count = len(rows)
        rps = count / elapsed if elapsed > 0 else 0
        out(f"fetched {fmt_num(count)} rows in {elapsed:.4f}s ({fmt_rps(rps)} rows/s)")
        results_dict[label] = {
            "query_time": elapsed,
            "rows_per_second": rps,
            "row_count": count,
        }


def _run_sync_query(conn, label, query, results_dict):
    with conn.cursor() as cursor:
        out(f"  {label}: Executing...", end=" ")
        start = time.perf_counter()
        cursor.execute(query)
        rows = cursor.fetchall()
        elapsed = time.perf_counter() - start
        count = len(rows)
        rps = count / elapsed if elapsed > 0 else 0
        out(f"fetched {fmt_num(count)} rows in {elapsed:.4f}s ({fmt_rps(rps)} rows/s)")
        results_dict[label] = {
            "query_time": elapsed,
            "rows_per_second": rps,
            "row_count": count,
        }


def _print_bar(label, rps, max_rps):
    bar_len = int((rps / max_rps) * 40) if max_rps > 0 else 0
    bar = "#" * bar_len + "-" * (40 - bar_len)
    out(f"  {label:30s} [{bar}] {fmt_rps(rps):>12s} rows/s")


async def _run_sync_driver(label, connect_fn, query, results_dict):
    try:
        start_conn = time.perf_counter()
        conn = connect_fn()
        ct = time.perf_counter() - start_conn
        flags = []
        if not nzpy_native._HAVE_NATIVE:
            flags.append("native ext disabled")
        tag = f" ({', '.join(flags)})" if flags else ""
        out(f"  {label}: Connected in {ct:.4f}s{tag}")
        _run_sync_query(conn, label, query, results_dict)
        conn.close()
    except Exception as e:
        out(f"  {label}: ERROR - {e}")
        results_dict[label] = {"error": str(e)}


async def _run_async_driver_extended(label, query, results_dict):
    try:
        start_conn = time.perf_counter()
        conn = await nzpy_extended.connect(
            user=USER, password=PASSWORD, host=HOST, port=PORT, database=DATABASE
        )
        ct = time.perf_counter() - start_conn
        flags = []
        if not nzpy_native._HAVE_NATIVE:
            flags.append("native ext disabled")
        tag = f" ({', '.join(flags)})" if flags else ""
        out(f"  {label}: Connected in {ct:.4f}s{tag}")
        await _run_async_query(conn, label, query, results_dict)
        await conn.close()
    except Exception as e:
        out(f"  {label}: ERROR - {e}")
        results_dict[label] = {"error": str(e)}


async def run_single_type(type_name, query):
    """Run one query across all available drivers, return {driver: result}."""
    results = {}

    # official nzpy (sync)
    await _run_sync_driver(
        "official_nzpy",
        lambda: nzpy.connect(
            user=USER, password=PASSWORD, host=HOST, port=PORT, database=DATABASE
        ),
        query,
        results,
    )

    # pyodbc (sync) — with ANSI-ODBC fallback for Linux/WSL2
    _odbc_conn_str = (
        f"DRIVER={{{ODBC_DRIVER_NAME}}};SERVER={HOST};PORT={PORT};"
        f"DATABASE={DATABASE};UID={USER};PWD={PASSWORD};"
    )
    if PYODBC_WORKING:
        await _run_sync_driver(
            "pyodbc",
            lambda: pyodbc.connect(_odbc_conn_str),  # type: ignore[union-attr]
            query,
            results,
        )
    elif _ANSI_ODBC_AVAILABLE:
        await _run_sync_driver(
            "pyodbc",
            lambda: _AnsiOdbcConnection(_odbc_conn_str),
            query,
            results,
        )

    # nzpy_extended async (with native extension)
    await _run_async_driver_extended("nzpy_extended_async", query, results)

    # nzpy_extended sync (with native extension)
    await _run_sync_driver(
        "nzpy_extended_sync",
        lambda: nzpy_extended.sync.connect(
            user=USER, password=PASSWORD, host=HOST, port=PORT, database=DATABASE
        ),
        query,
        results,
    )

    # nzpy_extended async (pure Python, no native extension)
    original_native = nzpy_native._HAVE_NATIVE
    nzpy_native._HAVE_NATIVE = False
    try:
        await _run_async_driver_extended("nzpy_extended_async_nonative", query, results)
    finally:
        nzpy_native._HAVE_NATIVE = original_native

    # nzpy_extended sync (pure Python, no native extension)
    original_native = nzpy_native._HAVE_NATIVE
    nzpy_native._HAVE_NATIVE = False
    try:
        await _run_sync_driver(
            "nzpy_extended_sync_nonative",
            lambda: nzpy_extended.sync.connect(
                user=USER, password=PASSWORD, host=HOST, port=PORT, database=DATABASE
            ),
            query,
            results,
        )
    finally:
        nzpy_native._HAVE_NATIVE = original_native

    return results


async def run_performance_test(output_path=None):
    out(f"--- nzpy_extended performance test (data-type breakdown) ---")
    out(f"Host: {HOST}:{PORT}, DB: {DATABASE}, Rows: {fmt_num(ROW_LIMIT)}")
    out(f"Official nzpy: available,  pyodbc: {PYODBC_AVAILABLE} (working: {PYODBC_WORKING})")
    if not PYODBC_WORKING and PYODBC_AVAILABLE:
        out(f"  pyodbc hint: {PYODBC_HINT}")
    out("")

    all_results = {}

    for type_name, query in QUERIES.items():
        header = f"  {type_name.replace('_', ' ').upper()}"
        out(f"─── {header} ───")
        drv_results = await run_single_type(type_name, query)
        all_results[type_name] = drv_results
        out("")

    # ── Per-type summary with driver comparison ────────────────────────────
    out("=" * 68)
    out("  PER-TYPE × DRIVER COMPARISON (rows/s)")
    out("=" * 68)

    header_row = f"  {'type':20s}"
    for d in DRIVER_NAMES:
        header_row += f"  {d:>16s}"
    out(header_row)
    out("  " + "-" * (20 + 18 * len(DRIVER_NAMES)))

    for type_name in QUERIES:
        row = f"  {type_name:20s}"
        for d in DRIVER_NAMES:
            r = all_results.get(type_name, {}).get(d)
            if r and "error" not in r:
                row += f"  {fmt_rps(r['rows_per_second']):>16s}"
            elif r:
                row += f"  {'ERROR':>16s}"
            else:
                row += f"  {'N/A':>16s}"
        out(row)

    # ── Visual bars per query type ─────────────────────────────────────────
    out("")
    for type_name in QUERIES:
        drv_results = all_results.get(type_name, {})
        valid = {k: v for k, v in drv_results.items() if "error" not in v}
        if not valid:
            continue
        max_rps = max(v["rows_per_second"] for v in valid.values())
        out(f"  [{type_name}]")
        for d in DRIVER_NAMES:
            r = valid.get(d)
            if r:
                _print_bar(d, r["rows_per_second"], max_rps)
        out("")

    # ── Detailed timings ───────────────────────────────────────────────────
    out("=" * 68)
    out("  DETAILED TIMINGS")
    out("=" * 68)

    for type_name in QUERIES:
        for driver_name, r in all_results.get(type_name, {}).items():
            if "error" not in r:
                out(
                    f"  {type_name:20s}  {driver_name:20s}  "
                    f"{r['query_time']:.4f}s  |  "
                    f"{fmt_rps(r['rows_per_second']):>12s} rows/s  |  "
                    f"{fmt_num(r['row_count'])} rows"
                )
            else:
                out(
                    f"  {type_name:20s}  {driver_name:20s}  "
                    f"ERROR - {r['error']}"
                )

    # ── Save to TXT if requested ───────────────────────────────────────────
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(OUTPUT_LINES) + "\n")
        out(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="nzpy_extended performance benchmark"
    )
    parser.add_argument(
        "-o", "--output",
        help="Save output to a TXT file (e.g. benchmark_results.txt)",
        default=os.getenv("NZ_OUTPUT"),
    )
    args = parser.parse_args()
    missing = [
        names[0] for key, names in _SETTING_NAMES.items()
        if not _setting(*names) or (key == "port" and PORT <= 0)
    ]
    if missing:
        raise SystemExit(
            "missing connection settings (set the NZ_DEV_* variable): " + ", ".join(missing)
        )
    asyncio.run(run_performance_test(output_path=args.output))