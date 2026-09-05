# Sync API Reference

Full reference for `nzpy_extended.sync` — the synchronous, DB-API 2.0 compliant API.

```python
import nzpy_extended.sync as nzpy
```

## connect()

```python
conn = nzpy.connect(
    user="admin",
    password="password",
    host="localhost",
    port=5480,
    database="mydb",
    connect_timeout=10.0,     # TCP connect timeout only
    on_connect=None,           # optional callback(conn)
)
```

Returns `SyncConnection`. All parameters after `port` are optional.

## SyncConnection

```python
conn = nzpy.connect(...)
```

### Properties

| Property | Type | R/W | Description |
|---|---|---|---|
| `autocommit` | `bool` | R/W | Default `True`. When `False`, explicit `commit()` needed |
| `timeout` | `float \| None` | R/W | Default timeout for new cursors. `None` = no limit |
| `closed` | `bool` | R | `True` after `close()` |

### Methods

#### `cursor() → SyncCursor`

Create a new cursor. Inherits `conn.timeout`.

```python
cur = conn.cursor()
```

#### `execute(sql, args=None, timeout=None) → SyncCursor`

One-shot convenience. Creates a cursor, executes, returns it (pyodbc pattern).

```python
row = conn.execute("SELECT 1").fetchone()
conn.execute("INSERT INTO t VALUES (?)", (42,))
```

#### `commit()`

Commit the current transaction.

```python
conn.autocommit = False
cur.execute("INSERT INTO t VALUES (1)")
conn.commit()
```

#### `rollback()`

Roll back the current transaction.

#### `cancel()`

Cancel the currently running query on this connection. Session survives.

#### `transaction() → context manager`

```python
with conn.transaction():
    cur.execute("INSERT INTO t VALUES (1)")
    cur.execute("INSERT INTO t VALUES (2)")
    # auto-commit on success, auto-rollback on exception
```

#### `load_data(table_name, rows, columns=None, ...) → int`

Bulk insert via external table protocol. Returns row count.

```python
count = conn.load_data("my_table", [(1, "Alice"), (2, "Bob")])
```

#### `close()`

Close the connection and underlying socket. Idempotent.

### Context manager

Commit failures propagate to the caller, and the connection is closed even if
commit fails. When application code raises, rollback is attempted without
replacing the original exception.

`with conn.transaction():` explicitly begins a transaction, commits on success
and rolls back on an exception. It restores the previous autocommit setting and
does not close the connection. Entering with an already active transaction raises
`NotSupportedError`; nested transactions/savepoints are not emulated.

```python
with nzpy.connect(...) as conn:
    ...
# auto-closes on exit, auto-commits/rollbacks on exception
```

## SyncCursor

Created via `conn.cursor()` or `conn.execute(...)`.

### Properties

| Property | Type | R/W | Description |
|---|---|---|---|
| `description` | `tuple \| None` | R | PEP 249 7-tuple column metadata. Available after `execute()` |
| `rowcount` | `int` | R | Number of rows affected by last operation |
| `rownumber` | `int` | R | PEP 249, 0-based current row index |
| `arraysize` | `int` | R/W | Rows per `fetchmany()` call. Default `100` |
| `messages` | `deque` | R | Server notices (RAISE NOTICE, INFO messages) |
| `timeout` | `float \| None` | R/W | Per-cursor timeout. Falls back to `conn.timeout` |
| `statusmessage` | `str \| None` | R | Status of last command |

### Methods

#### `execute(sql, args=None, timeout=None) → SyncCursor`

Execute SQL with optional parameters and timeout. Returns `self` (PEP 249 chaining).

**Timeout resolution** (highest priority first):
1. Explicit `timeout=` argument
2. `cur.timeout` property
3. `conn.timeout` property (inherited at cursor creation)

```python
cur.execute("SELECT id FROM users WHERE name = ?", ("Alice",))
rows = cur.execute("SELECT 1, 2, 3").fetchall()  # chaining
cur.execute(heavy_sql, timeout=5.0)               # with timeout
```

When timeout fires: cancels the query, raises `OperationalError`. Connection session survives.

#### `executemany(sql, seq_of_args) → SyncCursor`

Execute the same SQL for each parameter set.

```python
cur.executemany(
    "INSERT INTO t VALUES (?, ?)",
    [(1, "a"), (2, "b"), (3, "c")]
)
```

On partial failure: sets `rowcount` to successful count, enriches error with param set index.

#### `callproc(procname, parameters=None) → list | None`

Call a stored procedure. Returns copy of input parameters.

```python
cur.callproc("sp_add", [10, 20])   # → CALL sp_add(10, 20)
rows = cur.fetchall()              # result set
cur.callproc("sp_noop")            # → CALL sp_noop()
```

#### `fetchone() → row | None`

Fetch next row. Returns `None` after exhaustion.

#### `fetchmany(size=None) → list[row]`

Fetch up to `size` rows. Default: `arraysize` (100).

#### `fetchall() → list[row]`

Fetch all remaining rows. Buffers entire result set.

#### `nextset() → bool | None`

Skip to next result set (for multi-statement queries).

#### `get_schema_table() → list[dict]`

Rich metadata as list of dicts with keys: `ColumnName`, `ColumnOrdinal`, `ColumnSize`, `NumericPrecision`, `NumericScale`, `DataType`, `ProviderType`, `AllowDBNull`, `IsReadOnly`, `IsLong`, `IsAutoIncrement`.

```python
cur.execute("SELECT 1 AS id, 'x' AS name")
for col in cur.get_schema_table():
    print(col["ColumnName"], col["DataType"])
```

#### `cancel()` / `interrupt()`

Cancel the currently running query on this cursor's connection.

#### `close()`

Close the cursor. Drains active protocol generator.

### Iteration

`SyncCursor` is iterable. Uses `arraysize` for batch fetch (default 100 rows per round-trip):

```python
cur.execute("SELECT * FROM large_table")
for row in cur:
    process(row)
```

Returns rows as lists.

### Context manager

```python
with conn.cursor() as cur:
    cur.execute("SELECT 1")
    print(cur.fetchone())
# auto-closes cursor on exit
```

## Error hierarchy

```
Exception
 └── Warning
 └── Error
      ├── InterfaceError (includes ConnectionClosedError)
      └── DatabaseError
           ├── DataError
           ├── OperationalError    # timeout, connection issues
           ├── IntegrityError
           ├── InternalError
           ├── ProgrammingError    # SQL errors, invalid params, callproc
           └── NotSupportedError
```

## Standalone functions

### `load_data(conn, table_name, rows, ...) → int`

Function form of `conn.load_data()`.

```python
from nzpy_extended.sync import load_data
count = load_data(conn, "my_table", [(1, "a"), (2, "b")])
```
