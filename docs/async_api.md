# Async API Reference

Full reference for the native async API in `nzpy_extended`.

```python
import nzpy_extended as nzpy
```

For the synchronous DB-API 2.0 wrapper, see [sync_api.md](sync_api.md).

## connect()

```python
conn = await nzpy.connect(
    user="admin",
    password="password",
    host="localhost",
    port=5480,
    database="mydb",
    connect_timeout=10.0,
    application_name="my-app",
    ssl={"ca_certs": "/path/to/ca.pem", "ssl_verify": True},
    securityLevel=2,
)
```

Returns `Connection`. Use as an async context manager or call `await conn.close()` explicitly.

### SSL

- `ssl_verify` defaults to `True`.
- `ssl_allow_fallback` defaults to `False`. When `False`, the driver refuses to downgrade to an unsecured session if SSL negotiation fails.
- To allow legacy unsecured fallback (not recommended in production):

```python
await nzpy.connect(..., ssl={"ssl_allow_fallback": True}, securityLevel=2)
```

## Connection

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `autocommit` | `bool` | Default `True` (see PEP 249 deviations) |
| `in_transaction` | `bool` | Whether a transaction is open |
| `meta` | `ConnectionMetadataProvider` | Catalog introspection API |

### Methods

| Method | Description |
|--------|-------------|
| `cursor()` | Create a new `Cursor` |
| `commit()` / `rollback()` | Transaction control |
| `cancel()` | Cancel running query (separate cancel connection) |
| `close()` | Close connection (idempotent) |
| `load_data(...)` | Bulk load via external table protocol |

### Context manager

```python
async with await nzpy.connect(...) as conn:
    cur = conn.cursor()
    await cur.execute("SELECT 1")
    row = await cur.fetchone()
```

## Cursor

### Execution

```python
cur = conn.cursor()
await cur.execute("SELECT * FROM t WHERE id = ?", (42,))
rows = await cur.fetchall()
```

`timeout` may be passed per execute:

```python
await cur.execute("SELECT ...", timeout=30.0)
```

### Iteration

```python
async for row in cur:
    process(row)
```

Or:

```python
row = await cur.fetchone()
```

### Multi-result sets

```python
await cur.execute("SELECT 1; SELECT 2")
assert await cur.fetchone() == (1,)
assert await cur.nextset()
assert await cur.fetchone() == (2,)
```

### Notices

```python
cur.notice_handler = lambda msg: print("NOTICE:", msg)
await cur.execute("CALL my_proc()")
print(list(cur.notices))
```

## Pooling (async)

```python
from nzpy_extended.pool import NzPool, AsyncNullPool

pool = NzPool(min_size=2, max_size=10, host="...", user="...", password="...", database="...")
await pool.open()

async with pool.connection() as conn:
    cur = conn.cursor()
    await cur.execute("SELECT 1")

await pool.close_all()
```

See [pool.md](pool.md) for idle timeout, max lifetime, and validation options.

## FastAPI

```python
from nzpy_extended.pool import NzPool
from nzpy_extended import fastapi as nz_fastapi

pool = NzPool(...)
app = FastAPI(lifespan=nz_fastapi.lifespan(pool))

@app.get("/")
async def read(conn=Depends(nz_fastapi.get_connection)):
    cur = conn.cursor()
    await cur.execute("SELECT 1")
    return await cur.fetchone()
```

## Limitations

- **One active cursor per connection** — a new `execute()` drains the previous result set.
- **threadsafety = 1** — do not share a connection across threads; use a pool instead.
- **Paramstyle** — wire protocol uses `qmark`; other styles are rendered client-side.
- **Parameters are rendered client-side** — values are written into the SQL text as
  literals, not bound by the server. Strings are quoted with `''`; a backslash is an
  ordinary character, because Netezza has no `E'...'` escape strings.
- **Special floats** — `float('nan')` and `float('inf')` are sent as
  `CAST(... AS DOUBLE PRECISION)`. Netezza refuses to store NaN in a column
  (`Float8 result is a NaN`). `Decimal` NaN and infinity raise `NotSupportedError`.
  A `-inf` that is returned directly in a SELECT list comes back as `+inf`. The same
  value read from a table column keeps its sign. This is server behaviour.
- **KRB5** — not supported.

See [pep249_compliance.md](pep249_compliance.md) for DB-API details and known deviations.
