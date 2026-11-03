# Timeout and Cancel in nzpy_extended

## Overview

nzpy_extended provides multiple timeout and cancel mechanisms. This document covers both
the **async** and **sync** (via `nzpy_extended.sync`) APIs.

---

## Two distinct timeout concepts

| Timeout | Scope | Where set |
|---|---|---|
| **Connection timeout** | TCP connect, then the startup/TLS/authentication handshake, each bounded separately (one-time) | `connect(connect_timeout=5)` |
| **Command timeout** | Per SQL execution (+ subsequent fetches) | `execute(..., timeout=N)` or `conn.timeout` / `cur.timeout` |

`connect_timeout` (or `timeout` when `connect_timeout` is not given) bounds the TCP
connect and, separately, the startup/TLS/authentication handshake. With neither set,
the handshake is still limited to 60 seconds, and a server that accepts the
connection but never answers raises `OperationalError` instead of hanging. These
values do not limit query execution:

```python
conn = await nzpy.connect(..., connect_timeout=10.0)       # async
conn = nzpy.sync.connect(..., connect_timeout=10.0)         # sync
```

For query-level timeouts, use the mechanisms below.

---

## Command timeout — async API

```python
cur = conn.cursor()
await cur.execute("SELECT ...", timeout=5.0)   # raises on timeout
rows = await cur.fetchall()
```

`Cursor.execute()` accepts `timeout: float | None` (seconds). If the query does not complete
within the timeout, the driver:

1. Sends a **cancel request** to the backend (via a new TCP connection)
2. Raises `nzpy_extended.OperationalError("Command execution timeout")`

The same timeout also applies to subsequent **fetch** operations (`fetchone`, `fetchall`,
`async for row in cur`) — the limit spans the entire lifecycle: execute + fetch.

**Semantics:**
- `timeout=None` (default) — no limit
- `timeout=0` — no limit (treated the same as `None`)
- `timeout=5.0` — 5-second limit

---

## Command timeout — sync API

### 1. Connection-level default (`conn.timeout`)

```python
conn.timeout = 5.0                       # default for all new cursors
cur = conn.cursor()                      # inherits timeout=5.0
cur.execute("SELECT ...")                # uses conn.timeout
conn.timeout = None                      # disable default
```

### 2. Cursor-level default (`cur.timeout`)

```python
cur = conn.cursor()
cur.timeout = 10.0                       # overrides conn.timeout for this cursor
cur.execute("SELECT ...")                # uses 10.0
cur.timeout = 0                          # 0 = no limit
```

### 3. Per-execute (explicit argument)

```python
cur.execute("SELECT ...", timeout=3.0)   # same semantics as async
rows = cur.fetchall()
```

### Timeout resolution priority (highest first)

| Priority | Source | Example |
|---|---|---|
| 1 | Explicit argument | `cur.execute(sql, timeout=5.0)` |
| 2 | Cursor property | `cur.timeout = 10.0` |
| 3 | Connection property | `conn.timeout = 30.0` |

A cursor created before `conn.timeout` was set is **not** affected by later changes to
the connection property (inheritance happens at cursor creation time).

---

## Cancel

Both async and sync APIs support manual cancellation.

### Async

```python
await conn.cancel()          # cancel whatever is running on this connection
await cur.cancel()           # same, delegates to connection
```

### Sync

```python
conn.cancel()                # cancel whatever is running
cur.cancel()
cur.interrupt()              # alias for cancel()
```

### Cancel via timeout

When a command timeout fires, cancel is called **automatically** as part of the timeout
handling. You don't need to call cancel manually after a timeout.

### How cancel works

Cancel opens a **new TCP connection** to the database and sends a PostgreSQL cancel
message with the backend PID and secret key of the target connection. This is a
lightweight out-of-band operation that does not affect the original socket.

---

## Session survival after timeout / cancel

**Both timeout and cancel preserve the connection session.** Temporary tables, session
variables, and transaction state survive. After a timeout or cancel, you can immediately
run additional queries on the same connection.

```python
# Create temp table, then timeout a heavy query
cur.execute("CREATE TEMP TABLE demo (id INT)")
cur.execute("INSERT INTO demo VALUES (42)")

try:
    cur.execute(HEAVY_SQL, timeout=3.0)
    cur.fetchall()
except nzpy.OperationalError:
    pass  # timeout occurred

# Session is still alive
cur.execute("SELECT id FROM demo")
row = cur.fetchone()
assert row[0] == 42   # temp table survived
```

---

## Complete async example

```python
import asyncio
import nzpy_extended as nzpy

async def main():
    conn = await nzpy.connect(
        user="admin", password="password",
        host="localhost", port=5480, database="JUST_DATA",
        connect_timeout=10.0,     # TCP connect timeout
    )
    try:
        cur = conn.cursor()
        await cur.execute("CREATE TEMP TABLE t1 (x INT)")
        await cur.execute("INSERT INTO t1 VALUES (1)")

        try:
            await cur.execute("SELECT pg_sleep(999)", timeout=2.0)
        except nzpy.OperationalError:
            print("Query timed out — session still valid")

        await cur.execute("SELECT x FROM t1")
        row = await cur.fetchone()
        print(f"After timeout, temp table: {row[0]}")
    finally:
        await conn.close()

asyncio.run(main())
```

## Complete sync example

```python
import nzpy_extended.sync as nzpy

conn = nzpy.connect(
    user="admin", password="password",
    host="localhost", port=5480, database="JUST_DATA",
    connect_timeout=10.0,
)
try:
    conn.timeout = 3.0                     # default for all cursors

    cur = conn.cursor()
    cur.execute("CREATE TEMP TABLE t1 (x INT)")
    cur.execute("INSERT INTO t1 VALUES (1)")

    try:
        cur.execute("SELECT pg_sleep(999)")    # uses conn.timeout=3.0
    except nzpy.OperationalError:
        print("Timed out — session valid")

    # Override per-cursor
    cur2 = conn.cursor()
    cur2.timeout = None                     # no timeout for this cursor
    cur2.execute("SELECT 1")
    print(cur2.fetchone())

    # Override per-execute
    cur3 = conn.cursor()
    cur3.execute("SELECT 2", timeout=10.0)  # explicit timeout wins

    # Check session survived
    cur = conn.cursor()
    cur.execute("SELECT x FROM t1")
    print(f"After timeout: {cur.fetchone()[0]}")
finally:
    conn.close()
```

---

## Quick reference

| API | Timeout argument | Connection property | Cursor property |
|---|---|---|---|
| **Async** | `cur.execute(sql, timeout=N)` | — | — |
| **Sync** | `cur.execute(sql, timeout=N)` | `conn.timeout = N` | `cur.timeout = N` |

| Action | Async | Sync |
|---|---|---|
| Cancel | `await conn.cancel()` | `conn.cancel()` |
| Timeout raises | `OperationalError` | `OperationalError` |
| Session survives timeout? | Yes | Yes |
| Session survives cancel? | Yes | Yes |
| `timeout=0` / `timeout=None` | No limit | No limit |
