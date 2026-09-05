# nzpy_extended: High-performance IBM Netezza driver for Python

**nzpy_extended** is a hard fork of [IBM nzpy](https://github.com/IBM/nzpy) — enriched with new features, major performance improvements via a C extension, and expanded platform support.

## Key differences from upstream nzpy

| Feature | nzpy (IBM) | nzpy_extended |
|---|---|---|
| Row parsing performance (mixed types, macOS) | ~10 000 rows/s | ~63 000 rows/s (no C ext) → **~93 000 rows/s** (+ C ext) |
| Supported Python | 3.5+ | **3.12, 3.13, 3.14** |
| Platform wheels | ❌ None | ✅ Linux x64, macOS ARM, Windows x64 (pre-built) |
| Async support | ❌ | ✅ Fully async API |
| Streaming bulk loading | ❌ | ✅ `load_data()` accepts sync and async iterables; `load_csv()` streams local CSV files |
| Sync API (DB-API 2.0) | ✅ | ✅ Full feature parity with async |

### Windows 11

| Data type | official nzpy | nzpy_extended (no C ext) | nzpy_extended (+ C ext) | vs. ODBC |
|---|---|---|---|---|
| INTEGER | ~18 k rows/s | ~196 k rows/s | **~256 k rows/s** | ≈ ODBC (~239 k) |
| NUMERIC | ~10 k rows/s | ~75 k rows/s | **~97 k rows/s** | ≈ ODBC (~90 k) |
| STRING | ~18 k rows/s | ~75 k rows/s | **~79 k rows/s** | ≈ ODBC (~69 k) |
| DATETIME | ~18 k rows/s | ~135 k rows/s | **~255 k rows/s** | ≈ ODBC (~249 k) |
| BOOLEAN | ~23 k rows/s | ~243 k rows/s | **~401 k rows/s** | ≈ ODBC (~359 k) |
| Mixed | ~4 900 rows/s | ~36 k rows/s | **~45 k rows/s** | ≈ ODBC (~44 k) |

### WSL2 (Linux x86_64)

| Data type | official nzpy | nzpy_extended (no C ext) | nzpy_extended (+ C ext) | vs. ODBC |
|---|---|---|---|---|
| INTEGER | ~21 k rows/s | ~203 k rows/s | **~640 k rows/s** | ~92 k rows/s |
| NUMERIC | ~11 k rows/s | ~79 k rows/s | **~150 k rows/s** | ~63 k rows/s |
| STRING | ~22 k rows/s | ~71 k rows/s | **~160 k rows/s** | ~80 k rows/s |
| DATETIME | ~21 k rows/s | ~126 k rows/s | **~407 k rows/s** | ~111 k rows/s |
| BOOLEAN | ~25 k rows/s | ~186 k rows/s | **~418 k rows/s** | ~192 k rows/s |
| Mixed | ~5.4 k rows/s | ~33 k rows/s | **~49 k rows/s** | ~20 k rows/s |

Note: WSL2 results were obtained on the same Netezza server as Windows 11 (192.168.0.144) — the higher throughput reflects Linux network stack efficiency. ODBC results use a ctypes-based ANSI-ODBC fallback (because pyodbc's Unicode API is incompatible with the Netezza ODBC driver on Linux). This wrapper fetches every cell individually via `SQLGetData`, so ODBC numbers on WSL2 are **understated** vs. native pyodbc on Windows (which uses `SQLBindCol` batch fetching). The real ODBC-vs-native gap on Linux is likely smaller.

### macOS ARM64 (Apple M4)

Benchmarks run on a Mac mini (Apple M4, 16 GB RAM) — same Netezza server at 192.168.0.144. 100 k rows per query.

| Data type | official nzpy | nzpy_extended (no C ext) | nzpy_extended (+ C ext) |
|---|---|---|---|
| INTEGER | ~44 k rows/s | ~356 k rows/s | **~726 k rows/s** |
| NUMERIC | ~22 k rows/s | ~122 k rows/s | **~195 k rows/s** |
| STRING | ~39 k rows/s | ~143 k rows/s | **~183 k rows/s** |
| DATETIME | ~42 k rows/s | ~253 k rows/s | **~683 k rows/s** |
| BOOLEAN | ~53 k rows/s | ~449 k rows/s | **~786 k rows/s** |
| Mixed | ~10 k rows/s | ~63 k rows/s | **~93 k rows/s** |

The C extension accelerates integer, decimal, datetime, and boolean parsing by avoiding Python object allocations per field and `struct.unpack` overhead. String parsing is primarily network-bound, so the C extension offers minimal benefit there.

If the compiled extension is not available (unsupported platform or Python version), the driver **gracefully falls back** to pure Python with identical semantics.

## Installation

The package is available on [PyPI](https://pypi.org/project/nzpy-extended/):

```shell
pip install nzpy-extended
```

Pre-built wheels are provided for:

| Platform | Architecture | Python |
|---|---|---|
| Linux | x86_64 (manylinux) | 3.12 / 3.13 / 3.14 |
| macOS | ARM64 (Apple Silicon) | 3.12 / 3.13 / 3.14 |
| Windows | x86_64 | 3.12 / 3.13 / 3.14 |

For other platforms or Python versions, `pip install` will compile the C extension from source (requires a C compiler: GCC, Clang, or MSVC). On systems without a compiler the install will fail — use a supported platform or version.

## Quick Start

### Sync — scripts, ETL, Jupyter, Django

```python
import nzpy_extended.sync as nzpy

conn = nzpy.connect(
    user="admin", password="password",
    host="netezza-host", database="mydb",
)

# --- Basic query ---
with conn.cursor() as cur:
    cur.execute("SELECT 1")
    print(cur.fetchone())  # [1]

# --- One-shot convenience (pyodbc pattern) ---
row = conn.execute("SELECT version()").fetchone()
print(row[0])

# --- Parameter binding ---
cur.execute("SELECT id, name FROM users WHERE active = ?", (1,))
for row in cur:
    print(row)

# --- Cursor iteration (batched, arraysize=100) ---
cur.execute("SELECT * FROM large_table")
for row in cur:            # fetches 100 rows per round-trip
    process(row)

# --- Method chaining (PEP 249) ---
rows = cur.execute("SELECT 1, 2, 3").fetchall()

# --- Explicit transaction control ---
conn.autocommit = False
cur.execute("INSERT INTO users (name) VALUES ('Alice')")
cur.execute("INSERT INTO users (name) VALUES ('Bob')")
conn.commit()               # or conn.rollback()
conn.autocommit = True

# --- Transaction context manager ---
with conn.transaction():
    cur.execute("INSERT INTO users (name) VALUES ('Charlie')")
    # auto-commits on success, auto-rollbacks on exception

# --- Timeout (pyodbc-compatible properties) ---
conn.timeout = 5.0          # default for all new cursors
cur = conn.cursor()         # inherits timeout=5.0
cur.execute("SELECT ...")   # raises OperationalError after 5s

cur.timeout = 10.0          # per-cursor override
cur.execute("SELECT ...", timeout=3.0)  # per-execute override wins

# --- Stored procedures ---
result = cur.callproc("sp_add_numbers", [10, 20])
rows = cur.fetchall()

# --- Server notices (RAISE NOTICE) ---
for msg in cur.messages:
    print("Notice:", msg)

# --- PEP 249 rownumber ---
cur.execute("SELECT * FROM t")
print(cur.rownumber)  # 0
cur.fetchone()
print(cur.rownumber)  # 1

# --- Column metadata ---
desc = cur.description       # 7-tuple after execute()
schema = cur.get_schema_table()  # rich metadata as list[dict]

# --- Cancel / interrupt ---
conn.cancel()                # stops running query, session survives
cur.interrupt()              # alias

# --- Connection state ---
print(conn.closed)           # False
conn.close()
print(conn.closed)           # True
```

### Async — FastAPI, asyncio

```python
import asyncio
import nzpy_extended as nzpy

async def main():
    async with await nzpy.connect(
        user="admin", password="password",
        host="netezza-host", database="mydb",
    ) as conn:
        # --- Basic query ---
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, name FROM users WHERE active = ?", (1,))
            return await cur.fetchall()

        # --- One-shot convenience ---
        row = await (await conn.execute("SELECT version()")).fetchone()

        # --- Async iteration (streaming) ---
        cur = conn.cursor()
        await cur.execute("SELECT * FROM large_table")
        async for row in cur:
            process(row)

        # --- Timeout ---
        await cur.execute("SELECT pg_sleep(999)", timeout=3.0)
        # raises OperationalError after 3s

        # --- Stored procedures ---
        result = await cur.callproc("sp_add_numbers", [10, 20])

        # --- Notices ---
        for msg in cur.messages:
            print("Notice:", msg)

asyncio.run(main())
```

### FastAPI with connection pool

```python
from fastapi import FastAPI, Depends
import nzpy_extended as nzpy
import nzpy_extended.fastapi as nzpy_fastapi

pool = nzpy.NzPool(
    min_size=2, max_size=10,
    host="netezza-host", database="mydb",
    user="admin", password="password",
)

app = FastAPI(lifespan=nzpy_fastapi.lifespan(pool))

@app.get("/users")
async def get_users(conn=Depends(nzpy_fastapi.get_connection)):
    async with conn.cursor() as cur:
        await cur.execute("SELECT * FROM users LIMIT 100")
        return await cur.fetchall()
```

### Sync connection pool

```python
import nzpy_extended.sync as nzpy
from nzpy_extended import SyncPool

pool = SyncPool(
    min_size=2, max_size=10,
    host="netezza-host", database="mydb",
    user="admin", password="password",
)
with pool.connection() as conn:
    conn.execute("SELECT 1").fetchone()
pool.close_all()
```

### Bulk data loading via external table protocol

`load_data()` inserts rows from a Python iterable or async iterable into a Netezza table using the native external table protocol (`REMOTESOURCE 'python'`). It supports optional automatic table creation. Lists and tuples are materialized as CSV bytes; generators, async generators, and other iterables are streamed to Netezza in chunks.

```python
# --- Sync ---
import nzpy_extended.sync as nzpy
conn = nzpy.connect(...)
count = conn.load_data("my_table", [(1, "Alice"), (2, "Bob")])

# --- Async ---
import nzpy_extended as nzpy
conn = await nzpy.connect(...)
count = await nzpy.load_data(conn, "my_table", [(1, "Alice"), (2, "Bob")])

# --- Auto-infer types from data ---
rows = [(1, "Alice", 100.50), (2, "Bob", 200.75)]
count = conn.load_data("my_table", rows)
# Creates: col1 SMALLINT, col2 VARCHAR(255), col3 NUMERIC

# --- Explicit columns ---
count = conn.load_data(
    table_name="products",
    rows=[(101, "Widget", 9.99)],
    columns=[("id", "INT"), ("name", "VARCHAR(200)"), ("price", "NUMERIC(10,2)")],
)

# --- Generator for large datasets ---
def generate_rows(n):
    for i in range(n):
        yield (i, f"item_{i}")
count = conn.load_data("my_table", rows=generate_rows(50000))

# --- Async generator for streaming ---
async def generate_rows_async(n):
    for i in range(n):
        yield (i, f"item_{i}")
count = await nzpy.load_data(conn, "my_table", rows=generate_rows_async(50000))
```

**Parameters:**

| Parameter | Default | Description |
|---|---|---|
| `conn` / `table_name` / `rows` | (required) | Connection, target table, row iterable |
| `columns` | `None` | `[(name, nz_type), ...]` or `None` for auto-infer from data |
| `delimiter` | `'\|'` | Field delimiter |
| `encoding` | `'LATIN9'` | Text encoding (`'UTF8'` for NVARCHAR) |
| `create_if_missing` | `True` | Auto-create table if not exists |
| `temporary` | `False` | Create TEMP TABLE |
| `distribute_on_random` | `True` | Add `DISTRIBUTE ON RANDOM` |
| `logdir` | temp dir | Netezza log directory |
| `escape_char` | `'\\'` | Escape character for delimiter within values |

### Streaming CSV import via `load_csv()`

`load_csv()` streams a local CSV file directly into a Netezza table without loading the whole file into memory. It inspects a sample of rows to infer column types when `create_if_missing=True`, then uses the external table protocol to load the remaining data.

```python
# --- Sync ---
import nzpy_extended.sync as nzpy
conn = nzpy.connect(...)
count = conn.load_csv("my_table", "C:/data/input.csv",
                      delimiter=',', has_header=True, encoding='UTF8')

# --- Async ---
import nzpy_extended as nzpy
conn = await nzpy.connect(...)
count = await nzpy.load_csv(conn, "my_table", "data/input.csv",
                            delimiter=',', has_header=True, encoding='UTF8')
```

| Parameter | Default | Description |
|---|---|---|
| `conn` / `table_name` / `csv_path` | (required) | Connection, target table, path to local CSV |
| `delimiter` | `','` | Field delimiter |
| `has_header` | `True` | Use first row as column names when inferring schema |
| `sample_size` | `1000` | Rows read for type inference |
| `encoding` | `'UTF8'` | File encoding |
| `create_if_missing` | `True` | Auto-create table from inferred types |
| `temporary` | `False` | Create TEMP TABLE |
| `distribute_on_random` | `True` | Add `DISTRIBUTE ON RANDOM` |
| `escape_char` | `'\\'` | Escape character for delimiter within values |
| `logdir` | temp dir | Netezza log directory |

### Metadata API (catalog introspection)

The `conn.meta` object provides async access to Netezza system catalog views — tables, columns, views, procedures, distribution keys, storage stats, sessions, and more. All queries run against the current database; connect to `SYSTEM` for system-wide objects.

```python
import nzpy_extended as nzpy

async def main():
    conn = await nzpy.connect(user="admin", password="password",
                              host="netezza-host", database="mydb")

    # --- Schemas & databases ---
    schemas = await conn.meta.get_schemas()       # ["ADMIN", "INFORMATION_SCHEMA", ...]
    dbs     = await conn.meta.get_databases()     # ["JUST_DATA", "SYSTEM", ...]
    db_name = await conn.meta.get_current_database()   # "JUST_DATA"

    # --- Tables ---
    tables = await conn.meta.get_tables(schema="ADMIN")
    # [{"schema": "ADMIN", "table_name": "DIMDATE", "owner": "ADMIN",
    #   "objtype": "TABLE", "objid": 123456, "row_count": 500000}, ...]

    tables = await conn.meta.get_tables(
        schema="ADMIN", table_pattern="DIM%", include_system=False,
    )

    # --- Views (includes view definition SQL) ---
    views = await conn.meta.get_views(schema="ADMIN")
    # [{"schema": "ADMIN", "view_name": "V_SALES", "owner": "ADMIN",
    #   "objid": 789012, "definition": "CREATE VIEW V_SALES AS SELECT ..."}, ...]

    # --- Columns ---
    cols = await conn.meta.get_columns("DIMDATE", schema="ADMIN")
    # [{"column_name": "DATEKEY", "ordinal": 1,
    #   "data_type": "DATE", "nullable": "N"}, ...]

    # Dot-notation also works:
    cols = await conn.meta.get_columns("ADMIN.DIMDATE")

    # --- Distribution key ---
    dk = await conn.meta.get_distribution_key("FACT_SALES", schema="ADMIN")
    # ["CUSTOMER_ID"]  or [] for RANDOM distribution

    # --- Table sizes ---
    sizes = await conn.meta.get_table_sizes(schema="ADMIN")
    # [{"schema": "ADMIN", "table_name": "FACT_SALES",
    #   "used_bytes": 500000000, "allocated_bytes": 600000000,
    #   "size_mb": 476, "skew": 1.2}, ...]

    # --- Stored procedures ---
    procs = await conn.meta.get_procedures(schema="ADMIN")
    # [{"schema": "ADMIN", "proc_name": "SP_LOAD_DATA",
    #   "owner": "ADMIN", "signature": "SP_LOAD_DATA(VARCHAR(256))",
    #   "returns": "INTEGER", "source": "CREATE PROCEDURE ..."}, ...]

    # --- Sequences & synonyms ---
    seqs = await conn.meta.get_sequences(schema="ADMIN")
    syns = await conn.meta.get_synonyms(schema="ADMIN")

    # --- Sessions ---
    sessions = await conn.meta.get_sessions()
    # [{"session_id": 123, "username": "ADMIN", "database_name": "JUST_DATA",
    #   "conntime": datetime(...), "priority": 0, "status": "active"}, ...]

    # --- Users & groups ---
    users  = await conn.meta.get_users()
    groups = await conn.meta.get_groups()

    # --- Query history (requires history collection enabled) ---
    history = await conn.meta.get_query_history(limit=50, username="ADMIN")

    # --- Search across tables, views, procedures ---
    results = await conn.meta.search_objects("SALES%", schema="ADMIN")
    # [{"object_type": "TABLE", "schema": "ADMIN",
    #   "object_name": "FACT_SALES", "owner": "ADMIN", "objid": 123}, ...]

asyncio.run(main())
```

| Method | Returns | Notes |
|---|---|---|
| `get_schemas()` | `list[str]` | All schemas in current database |
| `get_databases()` | `list[str]` | All databases visible to user |
| `get_current_database()` | `str` | Current database name |
| `get_current_schema()` | `str` | Current schema (search path) |
| `get_tables(schema, pattern, include_system)` | `list[dict]` | Tables: `schema`, `table_name`, `owner`, `objtype`, `objid`, `row_count` |
| `get_views(schema, pattern)` | `list[dict]` | Views: `schema`, `view_name`, `owner`, `objid`, `definition` (SQL!) |
| `get_columns(table, schema)` | `list[dict]` | Columns: `column_name`, `ordinal`, `data_type`, `nullable`, `objid` |
| `get_distribution_key(table, schema)` | `list[str]` | Distribution column names (empty = RANDOM) |
| `get_table_sizes(schema, pattern)` | `list[dict]` | Sizes: `used_bytes`, `allocated_bytes`, `size_mb`, `skew` |
| `get_procedures(schema, pattern)` | `list[dict]` | Procs: `schema`, `proc_name`, `owner`, `signature`, `returns`, `source` |
| `get_sequences(schema)` | `list[dict]` | Sequences: `schema`, `seq_name`, `owner`, `objid` |
| `get_synonyms(schema)` | `list[dict]` | Synonyms: `schema`, `synonym_name`, `ref_database`, `ref_schema`, `referenced_object` |
| `get_sessions()` | `list[dict]` | Active sessions: `session_id`, `username`, `database_name`, `conntime`, `priority` |
| `get_users()` | `list[dict]` | Users: `username`, `objid` |
| `get_groups()` | `list[dict]` | Groups: `groupname`, `objid` |
| `get_query_history(limit, user)` | `list[dict]` | History: `session_id`, `username`, `query_text`, `submit_time`, `result_rows` |
| `search_objects(pattern, schema)` | `list[dict]` | Unified search: `object_type` (TABLE/VIEW/PROCEDURE), `schema`, `object_name` |

## API Reference

| Feature | Async | Sync | Notes |
|---|---|---|---|
| `connect()` | `nzpy.connect(...)` | `nzpy.sync.connect(...)` | |
| `cursor()` | `conn.cursor()` | `conn.cursor()` | Returns `Cursor` / `SyncCursor` |
| `execute(sql, params)` | `await cur.execute(...)` | `cur.execute(...)` | PEP 249, returns cursor |
| `executemany(sql, seq)` | `await cur.executemany(...)` | `cur.executemany(...)` | Partial failure preserves rowcount |
| `callproc(name, params)` | `await cur.callproc(...)` | `cur.callproc(...)` | `CALL proc_name(args)` |
| `fetchone()` | `await cur.fetchone()` | `cur.fetchone()` | Returns row or `None` |
| `fetchmany(n)` | `await cur.fetchmany(n)` | `cur.fetchmany(n)` | Default `arraysize=100` |
| `fetchall()` | `await cur.fetchall()` | `cur.fetchall()` | |
| `nextset()` | `await cur.nextset()` | `cur.nextset()` | |
| `description` | `cur.description` | `cur.description` | 7-tuple, available after execute |
| `rowcount` | `cur.rowcount` | `cur.rowcount` | Rows affected |
| `rownumber` | `cur.rownumber` | `cur.rownumber` | PEP 249, 0-based index |
| `messages` | `cur.messages` | `cur.messages` | Server notices |
| `arraysize` | `cur.arraysize` | `cur.arraysize` | Default 100 |
| `get_schema_table()` | `cur.get_schema_table()` | `cur.get_schema_table()` | Rich metadata |
| `conn.execute(sql)` | `await conn.execute(...)` | `conn.execute(...)` | One-shot convenience |
| `conn.timeout` | — | `conn.timeout = N` | Default timeout for cursors |
| `cur.timeout` | — | `cur.timeout = N` | Per-cursor timeout |
| `autocommit` | `conn.autocommit` | `conn.autocommit` | Get/set, default `True` |
| `closed` | — | `conn.closed` | Read-only |
| `commit()` / `rollback()` | `await conn.commit()` | `conn.commit()` | |
| `cancel()` | `await conn.cancel()` | `conn.cancel()` | Session survives |
| `transaction()` | — | `conn.transaction()` | Context manager |
| `load_data()` | `await nzpy.load_data(...)` | `nzpy.sync.load_data(...)` | Bulk insert via external table |
| `conn.load_data()` | `await conn.load_data(...)` | `conn.load_data(...)` | Method form |
| `conn.meta.get_tables()` | `await conn.meta.get_tables(...)` | — | Catalog metadata (async only) |
| `NzPool` / `SyncPool` | `nzpy.NzPool(...)` | `nzpy.SyncPool(...)` | Connection pooling |

## Documentation

- `docs/` folder in the repository:
  - [`timeout_and_cancel.md`](docs/timeout_and_cancel.md) — Timeout and cancel mechanisms
  - [`sync_api.md`](docs/sync_api.md) — Full sync API reference
  - [`pep249_compliance.md`](docs/pep249_compliance.md) — PEP 249 compliance table
  - [`pool.md`](docs/pool.md) — Connection pool documentation
  - [`load_data.md`](docs/load_data.md) — Bulk data loading
  - [`metadata_api.md`](docs/metadata_api.md) — Catalog introspection API
- [GitHub Wiki](https://github.com/KrzysztofDusko/nzpy_extended/wiki)
- [Issue tracker](https://github.com/KrzysztofDusko/nzpy_extended/issues)

## Requirements

- Python ≥ 3.12
- CPython (PyPy not supported for C extension, pure-Python fallback only)

## Testing

### Running the test suite

#### CI (GitHub Actions)

CI now discovers all `unit` tests, measures branch coverage and runs C/pure-Python
variants on Python 3.12–3.14 across Linux, macOS and Windows. Publishing depends
on this quality workflow. Wheel/sdist import checks run outside the source tree.
Use `NZ_DEV_DATABASE` (preferred) or `NZ_DEV_DB` for live tests; the preferred
name wins when both are set. See `CHANGELOG.md` for unreleased compatibility fixes.

Pull requests run **without a live Netezza database**:

- Unit tests (`paramstyle`, regressions, C/Python parity, buffer pool/stream, pool, csv_import)
- `mypy` and `pyright`
- Wheel and sdist import smoke

Integration tests (`smoke`, `full`, and differential parity) must be run
**locally** against your Netezza instance.

#### Local integration

Tests require a running Netezza instance. Set the connection environment variables:

```shell
export NZ_DEV_HOST=your_netezza_host
export NZ_DEV_PORT=5480
export NZ_DEV_DB=JUST_DATA
export NZ_DEV_USER=admin
export NZ_DEV_PASSWORD=password
```

Differential parity tests use the independent JustyBase Netezza driver, not
ODBC. Build the Node driver from
[`JustyBase.NetezzaDriver`](https://github.com/justybase/JustyBase.NetezzaDriver)
or its Node companion, then point the tests at its compiled package:

```shell
export NZ_REFERENCE_NODE_DRIVER=/path/to/justybase_netezza_node_driver
pytest tests/test_odbc_comparison_smoke.py -v
pytest tests/test_odbc_comparison_node.py -m reference_node -v
```

The bridge in `tests/reference_driver.js` runs the reference driver in a
separate Node process and exchanges JSON-lines, so the two implementations do
not share protocol or conversion code. The default local checkout path is
`../justybase_netezza_node_driver`.

Run all tests locally:
```shell
pytest tests/ -v
```

CI-equivalent (no database):
```shell
pytest tests/test_paramstyle.py tests/test_typeobjects.py tests/test_regressions_unit.py \
  tests/test_c_python_parity_unit.py tests/test_buffer_pool.py tests/test_buffered_stream.py \
  tests/test_csv_import_unit.py tests/test_pool_unit.py -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for marker profiles (`smoke`, `full`, `unit`, `benchmark`).

### Upgrading to 0.4.0

If you connect with `securityLevel=2` and relied on automatic fallback to an unencrypted session when SSL fails, you must now opt in explicitly:

```python
await nzpy.connect(
    ...,
    securityLevel=2,
    ssl={"ssl_allow_fallback": True},  # only if you need legacy behaviour
)
```

See [CHANGELOG.md](CHANGELOG.md) for the full list of changes.

### C Extension / Pure Python parity

The C extension and pure-Python fallback must produce identical results for all data types. Parity tests verify this in two ways:

**Unit tests** (`tests/test_c_python_parity_unit.py`) — compare individual C parser functions against Python reference implementations byte-by-byte. No database required.

```shell
pytest tests/test_c_python_parity_unit.py -v
```

**Integration tests** (`tests/test_c_python_parity_integration.py`) — run real SQL queries through both code paths and verify results match. Requires a database.

```shell
pytest tests/test_c_python_parity_integration.py -v
```

**Verification script** — runs both test suites in C-extension and pure-Python modes side-by-side:

```shell
python tools/verify_c_python_parity.py
```

**Disabling C extension at runtime:**

Set the environment variable `NZPY_EXTENDED_NO_CEXT=1` to force pure-Python mode even when the compiled extension is available. Useful for debugging or verifying fallback correctness.

```shell
NZPY_EXTENDED_NO_CEXT=1 pip install nzpy_extended
# or at runtime:
NZPY_EXTENDED_NO_CEXT=1 python -c "import nzpy_extended.core; print(nzpy_extended.core._HAVE_C_EXT)"  # False
```

## Reproducing benchmark results

The per-type benchmark table above is generated by [`tools/examples/performance_test.py`](tools/examples/performance_test.py).

### Prerequisites

- A running Netezza instance with a table named `JUST_DATA..FACTPRODUCTINVENTORY` (or adjust `SOURCE_TABLE` in the script)
- Python ≥ 3.12
- Install the required packages:

```shell
pip install nzpy_extended
```

The official IBM driver is also tested for comparison (`pip install nzpy`).

Optional (for ODBC comparison):
- `pip install pyodbc` — with `NetezzaSQL` ODBC driver installed (rows labeled `pyodbc`)
- Set `NZ_ODBC_DRIVER` if your ODBC driver uses a different name (default: `NetezzaSQL`). On Linux/WSL2 the name is defined in `/etc/odbcinst.ini`, e.g.:
  ```shell
  export NZ_ODBC_DRIVER=NetezzaSQL  # Linux/WSL2
  ```
- **Linux/WSL2**: pyodbc's Unicode API is incompatible with the Netezza ODBC driver. The benchmark auto-detects this and falls back to a ctypes-based ANSI-ODBC wrapper. No additional configuration needed.

### Steps

1. **Set connection environment variables:**

```shell
set NZ_HOST=your_netezza_host     # Windows
set NZ_PORT=5480
set NZ_USER=admin
set NZ_PASSWORD=password
set NZ_DATABASE=JUST_DATA

# or on Linux/macOS:
export NZ_HOST=your_netezza_host
export NZ_PORT=5480
export NZ_USER=admin
export NZ_PASSWORD=password
export NZ_DATABASE=JUST_DATA
```

All variables have defaults — only `NZ_HOST` is required if your setup differs from the defaults.

ODBC driver name can be set separately:
```shell
export NZ_ODBC_DRIVER=NetezzaSQL  # Linux/macOS — defaults to "NetezzaSQL"
```

2. **Run the benchmark:**

```shell
python tools/examples/performance_test.py
```

3. **Adjust row count** (default: 100 000):

```shell
set NZ_ROWS=100000     # Windows
export NZ_ROWS=100000  # Linux/macOS
```

### What the script does

1. Connects using each driver: `official_nzpy` (always), `pyodbc` (via ANSI-ODBC ctypes fallback on Linux), `nzpy_extended` (async + sync, with and without C extension).
2. Runs six query categories: `integer_types`, `numeric_types`, `string_types`, `datetime_types`, `boolean_types`, and `all_types` (mixed).
3. Prints per-query timing, a compact DRIVER × TYPE comparison table (matching the README layout), and visual bar charts.

### Saving results to a TXT file

Use the `--output` / `-o` flag or the `NZ_OUTPUT` environment variable:

```shell
python tools/examples/performance_test.py -o benchmark_results.txt

# or via env var:
set NZ_OUTPUT=benchmark_results.txt
python tools/examples/performance_test.py
```

### Force pure-Python mode

```shell
set NZPY_EXTENDED_NO_CEXT=1
python tools/examples/performance_test.py
```

### Pytest benchmark (alternative)

A simpler pytest-based benchmark is also available (10 k rows, nzpy_extended only):

```shell
pytest tests/test_benchmark.py -v -m benchmark
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
