# Metadata API — Catalog Introspection

The `conn.meta` object (`ConnectionMetadataProvider`) provides async access to Netezza system catalog views. It allows you to explore tables, columns, views, procedures, distribution keys, storage statistics, sessions, users, and more — all without writing manual `_v_*` queries.

## Quick Start

```python
import nzpy_extended as nzpy

async def main():
    conn = await nzpy.connect(user="admin", password="password",
                              host="netezza-host", database="mydb")

    # List all tables in a schema
    tables = await conn.meta.get_tables(schema="ADMIN")
    for t in tables:
        print(f"{t['schema']}.{t['table_name']}  ({t['row_count']} rows)")

    # Get columns for a specific table
    cols = await conn.meta.get_columns("MY_TABLE", schema="ADMIN")
    for c in cols:
        print(f"  {c['column_name']}  {c['data_type']}  nullable={c['nullable']}")

    # Find the distribution key
    dk = await conn.meta.get_distribution_key("MY_TABLE", schema="ADMIN")
    print(f"Distributed on: {dk if dk else 'RANDOM'}")

asyncio.run(main())
```

## Database Context

All catalog views are **database-scoped**. The connection must be connected to the target database (not `SYSTEM`) to see user objects.

```python
db     = await conn.meta.get_current_database()  # e.g. "JUST_DATA"
schema = await conn.meta.get_current_schema()    # e.g. "ADMIN"
```

Connect to `SYSTEM` to query system-wide objects (users, groups, databases).

---

## Method Reference

### Schemas & Databases

| Method | Returns | Description |
|---|---|---|
| `get_schemas()` | `list[str]` | All schemas in the current database |
| `get_databases()` | `list[str]` | All databases visible to the current user |
| `get_current_database()` | `str \| None` | Currently connected database name |
| `get_current_schema()` | `str \| None` | Current schema (search path) |

```python
schemas = await conn.meta.get_schemas()
# ["ADMIN", "DEFINITION_SCHEMA", "INFORMATION_SCHEMA"]

dbs = await conn.meta.get_databases()
# ["JUST_DATA", "SYSTEM"]
```

### Tables

```python
await conn.meta.get_tables(
    schema=None,           # filter by schema name (e.g. "ADMIN")
    table_pattern=None,    # LIKE pattern (e.g. "DIM%")
    include_system=False,  # include DEFINITION_SCHEMA, INZA, etc.
) -> list[dict]
```

**Returned dict keys**: `schema`, `table_name`, `owner`, `objtype`, `objid`, `row_count`

```python
tables = await conn.meta.get_tables(schema="ADMIN", table_pattern="FACT%")
for t in tables:
    print(f"{t['schema']}.{t['table_name']}  "
          f"type={t['objtype']}  rows={t['row_count']}")
```

### Views

```python
await conn.meta.get_views(
    schema=None,        # filter by schema name
    view_pattern=None,  # LIKE pattern
) -> list[dict]
```

**Returned dict keys**: `schema`, `view_name`, `owner`, `objid`, `definition`

The `definition` field contains the view's DDL (CREATE VIEW ... AS SELECT ...).

```python
views = await conn.meta.get_views(schema="ADMIN")
for v in views:
    print(f"  {v['view_name']}")
    print(f"    {v['definition'][:120]}...")
```

### Columns

```python
await conn.meta.get_columns(
    table_name,       # table or view name (required)
    schema=None,      # schema name, or None for search path
) -> list[dict]
```

Supports dot-notation: `"SCHEMA.TABLE"` is equivalent to `get_columns("TABLE", schema="SCHEMA")`.

**Returned dict keys**: `column_name`, `ordinal`, `data_type`, `nullable` (`"Y"` / `"N"`), `objid`

```python
cols = await conn.meta.get_columns("DIMDATE", schema="ADMIN")
# Also: cols = await conn.meta.get_columns("ADMIN.DIMDATE")
for c in cols:
    null = "NULL" if c["nullable"] == "Y" else "NOT NULL"
    print(f"  {c['ordinal']:>3}  {c['column_name']:30s} {c['data_type']:20s} {null}")
```

### Distribution Key

```python
await conn.meta.get_distribution_key(
    table_name,
    schema=None,
) -> list[str]
```

Returns the column name(s) of the distribution key. An **empty list** means `DISTRIBUTE ON RANDOM`.

```python
dk = await conn.meta.get_distribution_key("FACT_SALES", schema="ADMIN")
if dk:
    print(f"Distributed on: {', '.join(dk)}")
else:
    print("Random distribution")
```

### Table Sizes

```python
await conn.meta.get_table_sizes(
    schema=None,
    table_pattern=None,
) -> list[dict]
```

Uses `_v_table_storage_stat` for storage metrics and `_v_table.reltuples` for row counts (metadata-driven, not a live `COUNT(*)`).

**Returned dict keys**: `schema`, `table_name`, `used_bytes`, `allocated_bytes`, `size_mb`, `skew`

```python
sizes = await conn.meta.get_table_sizes(schema="ADMIN")
for s in sizes:
    print(f"  {s['table_name']:30s} "
          f"{s['size_mb']:>6} MB  skew={s['skew']}")
```

A high `skew` value (> 3) indicates data is unevenly distributed across SPUs.

### Stored Procedures

```python
await conn.meta.get_procedures(
    schema=None,
    proc_pattern=None,
) -> list[dict]
```

**Returned dict keys**: `schema`, `proc_name`, `owner`, `objid`, `signature`, `returns`, `builtin`, `source`

The `source` field contains the full procedure body (NZPLSQL). `builtin` is `True` for system procedures.

```python
procs = await conn.meta.get_procedures(schema="ADMIN")
for p in procs:
    print(f"  {p['proc_name']}({p['signature']}) -> {p['returns']}")
```

### Sequences

```python
await conn.meta.get_sequences(schema=None) -> list[dict]
```

**Returned dict keys**: `schema`, `seq_name`, `owner`, `objid`

```python
for s in await conn.meta.get_sequences():
    print(f"  {s['schema']}.{s['seq_name']}")
```

### Synonyms

```python
await conn.meta.get_synonyms(schema=None) -> list[dict]
```

**Returned dict keys**: `schema`, `synonym_name`, `ref_database`, `ref_schema`, `referenced_object`, `owner`, `objid`

```python
for s in await conn.meta.get_synonyms():
    print(f"  {s['synonym_name']} --> {s['ref_database']}.."
          f"{s['ref_schema']}.{s['referenced_object']}")
```

### Sessions

```python
await conn.meta.get_sessions() -> list[dict]
```

**Returned dict keys**: `session_id`, `username`, `database_name`, `conntime`, `priority`, `status`, `client_type`, `client_os_username`

```python
sessions = await conn.meta.get_sessions()
for s in sessions:
    print(f"  session={s['session_id']}  user={s['username']}  "
          f"db={s['database_name']}  status={s['status']}")
```

### Users

```python
await conn.meta.get_users() -> list[dict]
```

**Returned dict keys**: `username`, `objid`

```python
for u in await conn.meta.get_users():
    print(f"  {u['username']}")
```

### Groups

```python
await conn.meta.get_groups() -> list[dict]
```

**Returned dict keys**: `groupname`, `objid`

### Query History

```python
await conn.meta.get_query_history(
    limit=100,
    username=None,   # optional filter by user
) -> list[dict]
```

Requires history collection to be enabled on the database (`COLLECT HISTORY`). Returns an empty list if disabled.

**Returned dict keys**: `session_id`, `username`, `database_name`, `query_text`, `submit_time`, `start_time`, `result_rows`

```python
recent = await conn.meta.get_query_history(limit=20, username="ADMIN")
for q in recent:
    print(f"  [{q['submit_time']}] {q.get('query_text', '')[:100]}")
```

### Search Objects

```python
await conn.meta.search_objects(
    name_pattern,     # LIKE pattern (e.g. "SALES%", "%")
    schema=None,      # optional filter
) -> list[dict]
```

Unified search across tables, views, and procedures.

**Returned dict keys**: `object_type` (`"TABLE"`, `"VIEW"`, `"PROCEDURE"`), `schema`, `object_name`, `owner`, `objid`

```python
results = await conn.meta.search_objects("SALES%", schema="ADMIN")
for r in results:
    print(f"  [{r['object_type']}] {r['schema']}.{r['object_name']}")
```

---

### Other catalog helpers

The additional catalog helpers return dictionaries with the catalog columns:

- get_functions(schema=None)
- get_constraints(schema=None)
- get_all_distribution_keys(schema=None)
- get_organize_keys(schema=None)
- get_object_details(schema=None)
- search_objects_detailed(name_pattern, schema=None)

Detailed object search uses a case-insensitive substring match and includes
object descriptions and creation dates.

### Reconstruct and export DDL

Single-object DDL helpers are available for tables, views, stored procedures,
external tables, and synonyms:

- get_table_ddl(table_name, schema=None)
- get_view_ddl(view_name, schema=None)
- get_procedure_ddl(proc_name, schema=None)
- get_external_table_ddl(table_name, schema=None)
- get_synonym_ddl(synonym_name, schema=None)

The corresponding batch helpers are get_tables_ddl, get_views_ddl,
get_procedures_ddl, get_external_tables_ddl, and get_synonyms_ddl.
export_database_ddl combines selected object types into one SQL script.
External tables and synonyms are included by default; pass
include_external_tables=False or include_synonyms=False to omit them. Each
type accepts a name pattern or an explicit list of names.

For overloaded procedures, pass the full signature, for example
LOAD_ORDERS(INTEGER, VARCHAR(30)).

## System Catalog Views Used

The metadata API queries the following Netezza system catalog views internally. Column names may vary by NPS version; the queries in this driver are tested against NPS 11.2.

| View | Used by |
|---|---|
| _v_function | get_functions() |
| _v_relation_keydata | get_constraints(), table DDL |
| _v_external, _v_extobject | external-table DDL |
| _v_object_data | detailed object listing/search and comments |
| `_v_table` | `get_tables()` |
| `_v_view` | `get_views()` |
| `_v_relation_column` | `get_columns()` |
| `_v_table_dist_map` | `get_distribution_key()` |
| `_v_table_storage_stat` | `get_table_sizes()` |
| `_v_schema` | `get_schemas()` |
| `_v_database` | `get_databases()` |
| `_v_procedure` | `get_procedures()` |
| `_v_sequence` | `get_sequences()` |
| `_v_synonym` | `get_synonyms()` |
| `_v_session` | `get_sessions()` |
| `_v_user` | `get_users()` |
| `_v_group` | `get_groups()` |
| `_v_qryhist` | `get_query_history()` |

---

## Practical Recipes

### List all tables with their column counts

```python
tables = await conn.meta.get_tables(schema="ADMIN")
for t in tables:
    cols = await conn.meta.get_columns(t["table_name"], schema=t["schema"])
    print(f"{t['table_name']}: {len(cols)} columns, ~{t['row_count']} rows")
```

### Find tables distributed on RANDOM

```python
tables = await conn.meta.get_tables(schema="ADMIN")
for t in tables:
    dk = await conn.meta.get_distribution_key(
        t["table_name"], schema=t["schema"]
    )
    if not dk:
        print(f"RANDOM: {t['table_name']} ({t['size_mb']} MB)")
```

### Find the largest tables

```python
sizes = await conn.meta.get_table_sizes(schema="ADMIN")
for s in sorted(sizes, key=lambda x: x['used_bytes'], reverse=True)[:10]:
    print(f"{s['table_name']:30s} {s['size_mb']:>6} MB")
```

### Export view definitions

```python
views = await conn.meta.get_views(schema="ADMIN")
for v in views:
    defn = v.get('definition', '')
    if defn:
        print(f"-- {v['view_name']}")
        print(defn)
        print(";")
```

### Get procedure source code

```python
procs = await conn.meta.get_procedures(schema="ADMIN")
for p in procs:
    if not p.get('builtin') and p.get('source'):
        print(f"-- {p['proc_name']}")
        print(p['source'])
```

### Monitor active sessions

```python
sessions = await conn.meta.get_sessions()
for s in sessions:
    if s['database_name'] == 'JUST_DATA':
        print(f"session={s['session_id']} user={s['username']} "
              f"since={s['conntime']}")
```

### List all non-nullable columns in a table

```python
cols = await conn.meta.get_columns("MY_TABLE", schema="ADMIN")
for c in cols:
    if c['nullable'] == 'N':
        print(f"NOT NULL: {c['column_name']} ({c['data_type']})")
```
