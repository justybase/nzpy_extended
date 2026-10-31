# Bulk Data Loading (`load_data`)

`load_data()` loads rows from a Python iterable (a list, tuple, dict rows or a generator) into a Netezza table. The rows are streamed to the appliance through an external table (`REMOTESOURCE 'python'`), so the server reads them in one statement instead of running one `INSERT` per row. Use it for anything beyond a few hundred rows.

## Quick start

```python
import nzpy_extended.sync as nzpy

conn = nzpy.connect(user="admin", password="password", host="localhost", port=5480, database="mydb")
count = conn.load_data("my_table", [(1, "Alice"), (2, "Bob")])
print(f"Inserted {count} rows")
```

```python
import nzpy_extended as nzpy

async def main():
    conn = await nzpy.connect(user="admin", password="password", host="localhost", port=5480, database="mydb")
    try:
        count = await conn.load_data("my_table", [(1, "Alice"), (2, "Bob")])
    finally:
        await conn.close()
```

## API

```python
conn.load_data(table_name, rows, columns=None, delimiter='|', encoding=None,
               create_if_missing=True, temporary=False, distribute_on_random=True,
               logdir=None, escape_char='\\') -> int
```

The same function exists as `nzpy_extended.load_data(conn, ...)` (async) and `nzpy_extended.sync.load_data(conn, ...)` (sync). Both return the number of rows loaded.

| Parameter | Default | Description |
|---|---|---|
| `table_name` | required | `NAME`, `SCHEMA.NAME`, `DB.SCHEMA.NAME`, or `"quoted"` parts. Anything else is refused. |
| `rows` | required | Iterable of tuples, lists or dicts (dict keys are matched to column names, case-insensitively for unquoted names). Generators and async iterables are streamed. |
| `columns` | `None` | Names, `(name, type)` pairs, or a mix. `None` means every column is taken in order and typed by inference. |
| `delimiter` | `'\|'` | One character, not CR or LF. |
| `encoding` | `None` | `None` chooses automatically (see [Text encoding](#text-encoding)). Or `'UTF8'`, `'INTERNAL'`, `'LATIN9'`. |
| `create_if_missing` | `True` | Create the table when it does not exist. With `False` a missing table raises `ProgrammingError`. |
| `temporary` | `False` | Create `CREATE TEMP TABLE` instead of a permanent table. |
| `distribute_on_random` | `True` | Add `DISTRIBUTE ON RANDOM` to the generated `CREATE TABLE`. |
| `logdir` | system temp dir | Directory for the server-side external table log. |
| `escape_char` | `'\\'` | Escape character for delimiter, backslash and line breaks inside values. `None` disables escaping (such values are then refused). |

## Column types

### Explicit types

Declare the types when you know them. This is the safest choice for production loads:

```python
conn.load_data(
    "inventory",
    [(101, "Widget", 42, 9.99)],
    columns=[("id", "INT"), ("name", "VARCHAR(200)"), ("qty", "INT"), ("price", "NUMERIC(10,2)")],
)
```

Columns may also be given as names only. Their types are then inferred from the data.

### Existing tables

When the table already exists, **the catalog types are always used**. A declared type is only a name hint and is ignored. `columns` may name a subset of the table's columns, in any order; the rest are left NULL. An unknown name raises `ProgrammingError: column X does not exist in table T`.

### Inference

When the table does not exist and `columns` does not declare a type, every row is scanned (not only the first one):

| Values in the column | Netezza type |
|---|---|
| integers | `SMALLINT`, `INTEGER` or `BIGINT`, the narrowest that holds the range |
| integers beyond `BIGINT` | refused; declare a `NUMERIC` column |
| floats (mixed with integers too) | `DOUBLE PRECISION` |
| `Decimal` (mixed with integers too) | `NUMERIC(p,s)`, sized from the largest value and scale |
| `bool` | `BOOLEAN` |
| `date` | `DATE` |
| `time` | `TIME` |
| `datetime`, or `date` mixed with `datetime` | `TIMESTAMP` |
| text | `VARCHAR(n)` with `n` in bytes when all text is ASCII; `NVARCHAR(n)` with `n` in characters when any text is not ASCII |
| only `None` (or empty) | `VARCHAR(255)` |
| `bytes` | refused; decode to text first |

Text in string columns is also recognised: `"true"/"yes"/…` becomes `BOOLEAN`, integer strings become integer types, ISO dates and times become `DATE`/`TIME`/`TIMESTAMP`, and decimal strings become `NUMERIC`. Strings with a leading zero (`"007"`) stay text.

Unnamed columns are called `col1`, `col2`, and so on.

## Value rules

| Python value | Behaviour |
|---|---|
| `None` | `NULL` in every column type. |
| `''` (empty string) | `NULL` in numeric, boolean, date, time and timestamp columns (CSV convention). Stays `''` in text columns. |
| `bool` | `BOOLEAN`: `1`/`0`. Text columns get `'true'`/`'false'`. |
| `bool` in an integer column | refused. |
| `int` | Range-checked against the column type (`SMALLINT` -32768…32767, and so on). |
| `float` | Stored as-is. `inf`/`-inf` are supported; `NaN` is refused (pass `None` for NULL). |
| `Decimal` / `str` in a `NUMERIC(p,s)` column | Normalised (`Decimal("1E+2")` is stored as `100`). Too many decimal places or integer digits are refused, with the row and column named. |
| `date` | `DATE`. A `datetime` at midnight is accepted; one with a time part is refused. |
| `datetime` | `TIMESTAMP`, naive. Timezone-aware values are refused; convert them to naive first. |
| `time` | `TIME`, naive. |
| ISO strings | Accepted for `DATE`, `TIME` and `TIMESTAMP` columns (`"2024-01-02T03:04:05"` for timestamps). |
| `str` | Checked against the column length (see below). |

Length limits: `VARCHAR(n)` counts **bytes** in the encoding of the load (one byte per character for `LATIN9`, several for `UTF8`/`INTERNAL`); the maximum is 64000 bytes. `NVARCHAR(n)` counts **characters**; the maximum is 16000.

## Text encoding

With `encoding=None` the driver picks the encoding the server accepts for the column types:

| Text columns in the load | Encoding used |
|---|---|
| none, or only `VARCHAR` | `LATIN9` |
| only `NVARCHAR` | `UTF8` |
| `VARCHAR` and `NVARCHAR` together | `INTERNAL` |

Characters outside Latin-9 cannot be stored in a `VARCHAR` column; the load refuses them and names the row and column. Use `NVARCHAR` for arbitrary Unicode. An explicit `encoding='UTF8'` with `VARCHAR` columns is refused by the server and by the driver.

## Escaping and the NULL marker

Values are escaped so the server reads them back exactly:

- the escape character (default `\`) is doubled;
- the delimiter, CR and LF are preceded by the escape character.

The text `\N` is a normal string. It is escaped, so it round-trips as text, and only a real `None` becomes NULL. With `escape_char=None`, values that contain the delimiter or a line break, or the text `\N`, are refused instead of being written unescaped.

```python
conn.load_data("notes", [(1, "a|b"), (2, "line1\nline2"), (3, "\\N"), (4, None)],
               columns=[("id", "INT"), ("body", "VARCHAR(50)")])
```

## Errors

Validation runs before any row is stored, and for lists before the table is created. A refused input leaves no table and no rows behind.

| Error | Typical cause |
|---|---|
| `ProgrammingError` | Invalid table or column name, unsupported type, duplicate column, bad delimiter/escape/encoding, unknown column for an existing table, missing table with `create_if_missing=False`, no rows and no table to infer from. |
| `DataError` | A value that cannot be stored: wrong type, out of range, too long, outside Latin-9, timezone-aware datetime, `NaN` in a non-float column, bytes, a row with the wrong number of values. The message names the row and, where applicable, the column, for example `load_data: row 5001, column id: 'not-an-int' is not an integer`. |

### Failures during a stream

Generators are streamed and validated row by row, so a bad value can appear after thousands of rows. When that happens:

- the rows already sent are **not** committed, and a table created by this call does not remain;
- the server ends the session after an aborted stream, so **the connection is closed** and must be reconnected;
- the original exception (`DataError`, or whatever your generator raised) is re-raised.

If a generator raises its own exception, that exception is what you receive.

## Transactions

- With autocommit on and no open transaction, `load_data` runs `CREATE TABLE` (when needed) and the load as one transaction and commits it. A failure rolls back both.
- With autocommit off, or inside a transaction you opened yourself, the rows join that transaction. `load_data` does not commit; call `commit()` (`await conn.commit()` in async code) yourself.

## Large datasets

Generators are the right input for data that does not fit in memory:

```python
def generate_rows(n):
    for i in range(n):
        yield (i, f"item_{i:08d}")

count = conn.load_data("bulk_items", generate_rows(100_000),
                       columns=[("id", "INT"), ("name", "VARCHAR(20)")])
```

With explicit `columns`, a generator is streamed without being materialised. Without `columns`, all rows are read first so that types can be inferred.

## CSV files

`load_csv` loads a CSV file through the same path:

```python
count = conn.load_csv("sales", "/data/sales.csv", delimiter=',', has_header=True,
                      sample_size=1000, encoding='UTF8')
```

Types are inferred from the first `sample_size` data rows, and the rest of the file is validated as it streams. If a later row does not fit those types, the load fails with the row number, as above. Raise `sample_size`, or declare types with `load_data`, when the data may change type late in the file. An empty field is NULL. For an existing table with no header, the columns are matched by position.

## Performance

`load_data` sends the rows in one external table transfer, so the appliance does not run one statement per row. Measured on 2026-10-31 on the reference Netezza appliance, from a Mac mini (Apple M4) and from WSL2; full results are in [benchmarks.md](benchmarks.md).

| Method | Rows | Time (Mac mini / WSL2) |
|---|---|---|
| `executemany("INSERT ...")` | 1,000 | ~45 ms per row (about 45 s) |
| `load_data()`, list of rows | 100,000 | ~1.9 s / ~2.0 s |
| `load_data()`, generator | 100,000 | ~1.7 s / ~1.6 s |

`executemany` remains the right tool for a few statements, and for statements that are not plain inserts.
