# Bulk Data Loading (`load_data`)

`load_data()` inserts rows from a Python iterable into a Netezza table using the native external table protocol (`REMOTESOURCE 'python'`).

## Quick start

### Sync

```python
import nzpy_extended.sync as nzpy

conn = nzpy.connect(user="admin", password="password", host="localhost", port=5480, database="mydb")
count = conn.load_data("my_table", [(1, "Alice"), (2, "Bob")])
print(f"Inserted {count} rows")
```

### Async

```python
import nzpy_extended as nzpy

async def main():
    conn = await nzpy.connect(user="admin", ...)
    count = await nzpy.load_data(conn, "my_table", [(1, "Alice"), (2, "Bob")])
    # or as method:
    count = await conn.load_data("my_table", [(1, "Alice"), (2, "Bob")])

import asyncio
asyncio.run(main())
```

## API

### Standalone function

```python
# Sync
from nzpy_extended.sync import load_data
count = load_data(conn, table_name, rows, ...)

# Async
from nzpy_extended import load_data
count = await load_data(conn, table_name, rows, ...)
```

### Method on connection

```python
# Sync
count = conn.load_data("t", rows, ...)

# Async
count = await conn.load_data("t", rows, ...)
```

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `table_name` | `str` | required | Target table name |
| `rows` | `iterable` | required | Row data. First row used for type inference |
| `columns` | `list[tuple[str, str]] \| None` | `None` | Explicit `[(name, nz_type), ...]`. `None` = auto-infer |
| `delimiter` | `str` | `'\|'` | Field delimiter |
| `encoding` | `str` | `'LATIN9'` | Text encoding. Use `'UTF8'` for NVARCHAR/NCLOB columns |
| `create_if_missing` | `bool` | `True` | Auto-create table if not exists |
| `temporary` | `bool` | `False` | Create `TEMP TABLE` |
| `distribute_on_random` | `bool` | `True` | Add `DISTRIBUTE ON RANDOM` to DDL |
| `logdir` | `str \| None` | temp dir | Netezza external table log directory |
| `escape_char` | `str \| None` | `'\\'` | Escape character for delimiter within values. `None` to disable |

## Auto-infer column types

When `columns=None` and `create_if_missing=True`, the driver reads the first row and maps Python types to Netezza DDL:

| Python type | Netezza type | Notes |
|---|---|---|
| `int` (small) | `SMALLINT` | -32 768 to 32 767 |
| `int` (medium) | `INT` | -2 147 483 648 to 2 147 483 647 |
| `int` (large) | `BIGINT` | beyond INT range |
| `float` | `FLOAT` | |
| `str` | `VARCHAR(255)` | Length from actual data |
| `Decimal` | `NUMERIC(p,s)` | Precision/scale from value |
| `bool` | `BOOLEAN` | |
| `date` | `DATE` | |
| `datetime` | `TIMESTAMP` | |
| `bytes` | `BYTEA` | |
| `None` | `VARCHAR(1)` | Best-effort, consider explicit columns |

Column names default to `col1`, `col2`, ...

## Examples

### Auto-infer all types

```python
from decimal import Decimal
from datetime import date

rows = [
    (1, "Alice", Decimal("100.50"), True, date(2025, 1, 15)),
    (2, "Bob",   Decimal("200.75"), False, date(2025, 2, 20)),
]
count = conn.load_data("products", rows)
# Creates: col1 SMALLINT, col2 VARCHAR(255), col3 NUMERIC(5,2),
#          col4 BOOLEAN, col5 DATE
```

### Explicit columns

```python
count = conn.load_data(
    table_name="inventory",
    rows=[(101, "Widget", 42, 9.99)],
    columns=[
        ("id", "INT"),
        ("name", "VARCHAR(200)"),
        ("qty", "INT"),
        ("price", "NUMERIC(10,2)"),
    ],
)
```

### Generator for large datasets

```python
def generate_rows(n):
    for i in range(n):
        yield (i, f"item_{i:08d}")

count = conn.load_data("bulk_items", rows=generate_rows(100000))
print(f"Loaded {count} rows")
```

### Temporary table

```python
count = conn.load_data("temp_stage", rows, temporary=True)
```

### Custom delimiter and encoding

```python
count = conn.load_data("csv_import", rows, delimiter=',', escape_char='"', encoding='UTF8')
```

## Delimiter escaping

Netezza external tables do not support standard CSV double-quote quoting. When a value contains the delimiter character, it is escaped with the escape character (default: `\`). The driver handles this automatically.

With `escape_char=None`, delimiter in values will cause errors.

## Error handling

```python
try:
    count = conn.load_data("target", rows)
except nzpy.ProgrammingError as e:
    print(f"Load failed: {e}")
```

The function raises `ProgrammingError` if no rows are provided or type inference fails.

## Performance

`load_data()` sends the rows through one external table transfer, so the appliance does not run one `INSERT` per row. Measured on a Netezza 11.2 appliance, 2,000 rows of `(int, str)`:

| Method | Time | Per row |
|---|---|---|
| `executemany("INSERT ...")` | ~87 s | ~43 ms |
| `load_data()` | ~0.7 s | ~0.35 ms |

Use `load_data()` for anything beyond a few hundred rows. `executemany` remains the right choice for small batches and for statements that are not plain inserts.
