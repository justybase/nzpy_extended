# PEP 249 Compliance

DB-API 2.0 (PEP 249) compatibility of `nzpy_extended.sync`. The root module
provides an asynchronous extension of this interface, not a synchronous DB-API
entry point. Historical autocommit and arraysize defaults remain deviations.

## Module Interface

| Requirement | Status | Notes |
|---|---|---|
| `connect(*args, **kwargs)` | ✅ | Async and sync variants |
| `apilevel` = `"2.0"` | ✅ | |
| `threadsafety` = `1` | ✅ | Threads may share the module, not connections |
| `paramstyle` = `"qmark"` | ✅ | Question mark style (`WHERE x = ?`) |

### Exceptions

All mandatory exceptions are implemented with the correct inheritance hierarchy:

```
Exception
 ├── Warning
 └── Error
      ├── InterfaceError (includes ConnectionClosedError)
      └── DatabaseError
           ├── DataError
           ├── OperationalError
           ├── IntegrityError
           ├── InternalError
           ├── ProgrammingError
           └── NotSupportedError
```

| Exception | Status |
|---|---|
| `Warning` | ✅ |
| `Error` | ✅ |
| `InterfaceError` | ✅ |
| `DatabaseError` | ✅ |
| `DataError` | ✅ |
| `OperationalError` | ✅ |
| `IntegrityError` | ✅ |
| `InternalError` | ✅ |
| `ProgrammingError` | ✅ |
| `NotSupportedError` | ✅ |

## Connection Objects

| Requirement | Status | Notes |
|---|---|---|
| `.close()` | ✅ | Idempotent. Logs socket warnings instead of silently suppressing |
| `.commit()` | ✅ | |
| `.rollback()` | ✅ | Optional per spec, implemented |
| `.cursor()` | ✅ | |

## Cursor Objects

### Attributes

| Requirement | Status | Notes |
|---|---|---|
| `.description` | ✅ | 7-tuple. Available after `execute()` for queries returning rows. `None` for DML/DDL |
| `.rowcount` | ✅ | Rows affected, or `-1` if undetermined |

### Methods

| Requirement | Status | Notes |
|---|---|---|
| `.callproc(procname[, params])` | ✅ | Optional per spec. `CALL proc(args)` with parameter binding |
| `.close()` | ✅ | |
| `.execute(operation[, params])` | ✅ | Returns cursor (enables chaining). `params` as sequence |
| `.executemany(operation, seq)` | ✅ | Partial failure preserves rowcount, enriches error with index |
| `.fetchone()` | ✅ | Returns row or `None` |
| `.fetchmany([size])` | ✅ | Default size from `arraysize` |
| `.fetchall()` | ✅ | |
| `.nextset()` | ✅ | Optional per spec |
| `.arraysize` | ✅ | Default `100` (see deviations) |
| `.setinputsizes(sizes)` | ✅ | No-op (accepted, ignored) |
| `.setoutputsize(size[, col])` | ✅ | No-op (accepted, ignored) |

## Type Objects and Constructors

All mandatory type constructors and singletons are implemented.

| Requirement | Status | Notes |
|---|---|---|
| `Date(year, month, day)` | ✅ | Returns `datetime.date` compatible |
| `Time(hour, minute, second)` | ✅ | Returns `datetime.time` compatible |
| `Timestamp(year, month, day, hour, minute, second)` | ✅ | Returns `datetime.datetime` compatible |
| `DateFromTicks(ticks)` | ✅ | Wraps `Date(*time.localtime(ticks)[:3])` |
| `TimeFromTicks(ticks)` | ✅ | Wraps `Time(*time.localtime(ticks)[3:6])` |
| `TimestampFromTicks(ticks)` | ✅ | Wraps `Timestamp(*time.localtime(ticks)[:6])` |
| `Binary(string)` | ✅ | Wraps `bytes(string)` |
| `STRING` type | ✅ | Category matching text, CHAR and VARCHAR OIDs |
| `BINARY` type | ✅ | Category matching binary OID; legacy `isinstance` supported |
| `NUMBER` type | ✅ | Category matching integer, floating point and NUMERIC OIDs |
| `DATETIME` type | ✅ | Category matching date, time and timestamp OIDs |
| `ROWID` type | ✅ | Category matching OID 26 |

## Optional Extensions

| Extension | Status | Notes |
|---|---|---|
| `cursor.rownumber` | ✅ | 0-based index. Resets on `execute()`, increments on fetch |
| `cursor.messages` | ✅ | Server notices (deque). Cleared on `execute()` |
| `connection.Error`, `connection.ProgrammingError`, etc. | ✅ | Exception classes exposed as Connection attributes |
| `cursor.connection` | ✅ | Warns "DB-API extension cursor.connection used" |
| `connection.autocommit` | ✅ | Get/set property. Default `True` (see deviations) |
| `cursor.__iter__()` | ✅ | Sync cursor only (async uses `__aiter__`) |
| `cursor.scroll(value[, mode])` | ❌ | Not implemented |
| `cursor.next()` | ❌ | Use `fetchone()` or `async for`/`for` iteration |
| `cursor.lastrowid` | ❌ | Not implemented |
| `connection.messages` | ❌ | Use `cursor.messages` |
| `connection.errorhandler` / `cursor.errorhandler` | ❌ | Not implemented. Use try/except |
| TPC (two-phase commit) methods | ❌ | Not applicable to Netezza |

## Deviations

| Item | PEP 249 requirement | nzpy_extended | Reason |
|---|---|---|---|
| `arraysize` default | `1` | `100` | Performance: reduces network round-trips for cursor iteration |
| `autocommit` default | Must be initially **off** (`False`) | `True` | Retained for compatibility. Set `conn.autocommit = False` or use the sync `conn.transaction()` context |
| `rollback()` | Optional (raise `NotSupportedError` if unsupported) | Implemented | Netezza supports transactions |
| `callproc()` | Optional (raise `NotSupportedError` if unsupported) | Implemented | Netezza supports stored procedures via nzplsql |
| `nextset()` | Optional (raise `NotSupportedError` if unsupported) | Implemented | Netezza supports multiple result sets |
| `Binary()` | Must construct a binary object | Returns `bytes(string)` | Simpler than a custom wrapper class |
| `BINARY` type | Must be a Type Object | Category object | Compare with `description[i][1]`; `isinstance(value, BINARY)` also works |
