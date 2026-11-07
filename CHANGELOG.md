# Changelog

All notable changes to `nzpy_extended` are documented in this file.

## 0.9.2 - 2026-11-07

### Tests

- Cover a server shutdown in the middle of a query response. The driver must
  raise promptly, and a pool must discard the closed connection rather than
  hand it to the next borrower.

## 0.9.1 - 2026-11-07

This release changes packaging and the release process only. The driver code is
unchanged.

### Added

- Linux aarch64 wheels (manylinux and musllinux), so `pip install` on ARM64 Linux
  no longer needs a Rust toolchain.

### Changed

- Project URLs point to `justybase/nzpy_extended`, and the documentation link points
  to `docs/` in the repository. The README no longer links to a wiki.
- Publishing now runs only for `v*` tags. A manual run of the workflow builds and
  verifies the distributions without publishing, unless `publish` is set on a tag.
- Creating a GitHub Release no longer triggers publishing. The workflow creates the
  release itself after publishing, with the notes from this file.
- Every wheel must load its native decoder in a test install before it is published.

### Tests

- A regression test for a cargo-fuzz out-of-memory input: the varying-field count in
  a row header must not drive an allocation.

## 0.9.0 - 2026-11-03

This release fixes driver-semantics defects found in an audit and reproduced on a
real appliance. Several fixes change behaviour; read **Breaking changes** before
upgrading.

### Breaking changes

- **Server errors raise the right DB-API class, and `str(error)` is the message.**
  Netezza sends errors as plain text without a SQLSTATE. They were parsed as
  PostgreSQL field lists, so every server error was a `ProgrammingError` whose text
  was a dict such as `{'E': 'RROR:  Divide by 0'}`. Errors are now classified from
  the message: `DataError` (divide by zero, bad input, overflow, value too long),
  `IntegrityError` (NOT NULL), `OperationalError` (cancelled query) and
  `ProgrammingError` (everything else). `error.diag` holds `S` and `M`.
  Errors raised while fetching a later result of a multi-statement query keep
  their class instead of being wrapped in `ProgrammingError`.
- **VARBINARY and ST_GEOMETRY values are `bytes`.** They were decoded as text.
- **A wrong number of parameters raises `ProgrammingError`.** Extra values used to
  be ignored with a log warning.
- **A NUL character in a text parameter raises `DataError`**, and NUL in the query
  text raises `ProgrammingError`. NUL ends the query frame, so the server used to
  reject the statement and end the session.
- **Fetching from a cursor whose rows were discarded raises `InterfaceError`.**
  When another statement ran on the same connection before a cursor was fully
  fetched, its remaining rows were dropped and the next fetch returned `[]`.
- **The connection logger is no longer forced to DEBUG.** `logLevel` now defaults
  to `None`, which leaves the level to the application's logging configuration;
  the legacy `0`/`1`/`2` values and logging levels still work. Before, every
  connection logged at DEBUG into the application's handlers, including the full
  SQL text with rendered parameter values.
- **Client files are read or written only when the statement names them.** For the
  external-table protocol the server tells the client which file to send, write or
  use for logs. The driver used to open any path the server named, so a hostile or
  impersonated server could read or overwrite client files. Now the path must be a
  string literal of the statement being run (the `EXTERNAL '...'` file, `LOGDIR`),
  and server log file names must be plain names. `load_data`, `load_csv` and
  `REMOTESOURCE` statements are unaffected.
- `NZPY_EXTENDED_NO_CEXT` was removed, as announced in 0.8.0. Use
  `NZPY_EXTENDED_NO_NATIVE`.

### Fixed

- **Transaction state is tracked from the server's command tags.** A `BEGIN` sent by
  the application was not seen, so `in_transaction` stayed `False`,
  `rollback()` did nothing, and a pool returned the connection, still inside the
  transaction, to the next borrower, who saw its uncommitted rows. Pools now roll
  back any open transaction on release, and retire the connection if the rollback
  fails.
- **Statements in an aborted transaction raise `InternalError`.** After an error
  inside a transaction Netezza ignores every statement until the block ends and
  answers `*ABORT STATE*`; a `SELECT` used to return no rows without an error.
  `COMMIT` of such a transaction (which the server turns into a rollback) now
  raises `InternalError` saying that no changes were saved.
- **The metadata API escapes every filter.** `get_tables`, `get_views`,
  `get_table_sizes`, `get_procedures`, `get_sequences`, `get_synonyms` and
  `get_query_history` inserted `schema`, the name patterns and `username` into the
  SQL unescaped (SQL injection).
- **A client-side parameter error no longer costs the connection.** The session was
  marked as awaiting a response before the parameters were rendered, so after a
  `ProgrammingError` from rendering the next statement waited two seconds for a
  reply that never came and the connection was closed.
- **The startup/TLS/authentication handshake has a timeout.** `connect_timeout`
  (or `timeout`) covered only the TCP connect; a server that accepted the
  connection and never answered blocked `connect()` and pool acquisition forever.
  The handshake is now bounded by the same value, or 60 seconds when neither is
  set, and raises `OperationalError`.
- An unknown backend message in a result closes the connection with
  `InterfaceError` instead of misreading the rest of the stream.
- An interrupted sync call (`KeyboardInterrupt`) cancels its coroutine, which used
  to keep running and owning the connection.
- `?`, quotes and `--` inside `/* ... */` comments are no longer treated as
  placeholders or string delimiters.
- A TIMESTAMP stored in a width other than 8 bytes raises `DataError` instead of
  returning `None`; native decode errors name the column.
- `LogOptions.Logfile` adds its file handler once per logger, and the file rotates
  (100 MiB, five backups) instead of growing without bound.
- A refused or unopenable unload file is drained instead of hanging or
  desynchronising the session.

### Tests

- The offline mock sends errors and notices in the appliance's plain-text format.
- New unit tests: error classification from captured appliance messages
  (`test_errors_unit.py`), escaping of every metadata filter, refused file
  requests (`test_extab_paths_unit.py`) and the session-safety fixes
  (`test_audit_fixes_unit.py`). New live tests: `test_driver_semantics_live.py`.

## 0.8.0 - 2026-11-02

### Added

- `tools/examples/benchmark_load_data.py` times `load_data` against `executemany`
  on a live appliance. `docs/benchmarks.md` records the results for a Mac mini (M4)
  and for WSL2, with the commands to reproduce them.
- Support Python 3.11 through 3.15. One stable-ABI wheel per platform
  (`cp311-abi3`) installs on all five versions.
- Offline Netezza wire-protocol mock (`tests/mock_netezza.py`) used by new unit
  tests for the handshake, query phase, pool probing and cancel/timeout recovery,
  with no database required.
- Golden wire fixtures captured from a real appliance (`tests/data/wire`) are
  replayed offline under several fragmentation sizes, and every truncation point
  must fail with a driver error.
- Seeded fuzz and property tests for malformed responses and the native and
  pure-Python DBOS decoders (`tests/test_wire_fuzz_unit.py`).
- cargo-fuzz targets for DBOS frame scanning and row layout validation
  (`rust/fuzz/`).
- `tools/examples/benchmark_native_vs_python.py`: live, interleaved comparison of
  the native and pure-Python decoders. It creates and drops its own uniquely named
  table, so run it only against a disposable database.

- Offline `load_data` tests (`tests/test_bulk_unit.py`) and live round trips
  (`tests/test_load_data_roundtrip.py`, marker `full`) driven by shared fixtures
  (`tests/load_data_fixtures.py`): golden text, inferred types, escape/parse
  property checks, streaming, refused inputs, and client or bad-value failures
  mid-stream. Every live table is `ZZ_LDX_*`, dropped in `finally`, and checked
  against `_v_table`.
- `load_data` accepts dict rows keyed by column name, and async iterables.
- `load_data(..., escape_char=...)` controls how the delimiter, backslash and
  line breaks are escaped; `None` refuses such values instead of writing them.

### Changed

- Wheels use the Python stable ABI (`cp311-abi3`): one wheel per platform
  instead of one per Python version, installable on 3.11 and newer. Free-threaded
  CPython builds (3.13t/3.14t) are not covered by these wheels.
- The Rust decoder parses column metadata once per result set in `DbosDecoder`
  instead of once per row, and builds `date`/`time`/`datetime` values through the
  CPython C API instead of Python class calls. Both changes reduce per-row
  decoding cost in native and stable-ABI builds.
- The DBOS row decoder has a native implementation in Rust (PyO3, `rust/`),
  built with maturin as `nzpy_extended.rust_ext`. It is used by default.
  `NZPY_EXTENDED_NO_NATIVE=1` selects the pure-Python decoder instead. Both
  decoders produce the same results; the pure-Python decoder is the reference.
  Inputs outside the widths a Netezza 11.2 appliance sends (for example a 12-byte
  `NUMERIC` chunk, a `TIMESTAMP` that is not 8 bytes, `CHAR` size 0, or a negative
  4-byte `TIME`) decode as the reference does.
- The environment variable `NZPY_EXTENDED_NO_CEXT` is renamed to
  `NZPY_EXTENDED_NO_NATIVE`. The old name still selects pure Python in this
  release but emits a `FutureWarning`; it will be removed in 0.9.
- If the native decoder cannot be imported, the driver emits a `RuntimeWarning`
  and falls back to the much slower pure-Python decoder. Setting
  `NZPY_EXTENDED_NO_NATIVE=1` selects pure Python deliberately, without a warning.
- Packaging uses the maturin build backend. The version is read from
  `rust/Cargo.toml`; `setup.py`, `setup.cfg`, `MANIFEST.in` and versioneer are
  removed, and `nzpy_extended.__version__` is read from the installed
  distribution metadata. Installing from a source distribution needs a Rust
  toolchain.
- Handshake option decisions (option order, authentication payloads, version
  downgrade rules) moved into `nzpy_extended/_handshake_plan.py`, a pure module
  that mirrors the Rust driver's shared handshake planning. `handshake.py` now
  only performs socket I/O.
- `psutil` is part of the `test` extra so the memory-leak tests are collected.
- `load_data` was rewritten. Column types are inferred from every row, not the
  first one. Non-ASCII text infers `NVARCHAR`, and `VARCHAR` is used only for
  ASCII text. Integers that fit no type are refused. The default `encoding` is
  now `None`, which picks `LATIN9`, `UTF8` or `INTERNAL` from the column types
  (`UTF8` with `VARCHAR` is refused). See [docs/load_data.md](docs/load_data.md).
- Existing tables always use their catalog types. `columns` may name a subset of
  the columns, in any order; an unknown name raises `ProgrammingError`.
- `None` loads as NULL in every column type. An empty string is NULL in non-text
  columns and stays `''` in text columns. The text `\N` is stored as text.
- With autocommit on, `CREATE TABLE` and the load run in one transaction, so a
  failed load leaves neither a table nor rows.
- A failure while streaming closes the connection. The server ends the session
  after an aborted stream, so the next statement needs a new connection. The
  original exception is raised.
- `nzpy_extended.utils.rows_to_csv_bytes` and `rows_to_csv_chunks` were removed;
  they were internal helpers of the old `load_data`. `infer_columns_from_rows` is
  kept and now scans every row.
- `load_csv` infers types from the first `sample_size` rows, then validates the
  rest of the file as it streams. An empty CSV field is NULL.

### Performance

- CHAR, VARCHAR and NVARCHAR values decode on fast paths when the codec is UTF-8,
  ISO-8859-1 (`latin` is the default `char_varchar_encoding`) or ASCII-compatible
  and the bytes are ASCII. Results are unchanged; a text-heavy 100k-row fetch
  spends about half as long in the decoder.
- TLS reads and writes that would block wait on the event loop's selector. They
  no longer take a helper thread per wait. A query over a TLS connection on the
  loopback mock went from about 633 µs to about 517 µs.
- A new connection no longer runs four informational `SELECT`s whose results only
  went to debug logging. On the reference appliance, connecting went from about
  115–123 ms to about 86 ms.

- `load_data` streams rows in batches of 4,096, and each batch goes to the socket
  as one write. A 50,000-row load took about 1.5 s on the reference appliance.

### Fixed

- TLS connections work again on Windows. The default `ProactorEventLoop` has no
  `add_reader`, so a TLS read or write that had to wait raised
  `NotImplementedError`; such loops now wait in a helper thread.
- The macOS `universal2` wheel targets macOS 10.12, the minimum for Rust's x86_64
  target, and the Windows ARM64 wheel is built on a native ARM64 runner.
- The sync API no longer masks errors. When a coroutine raised `RuntimeError`,
  `runner.run()` re-submitted the already-finished coroutine and surfaced
  `cannot reuse already awaited coroutine` instead of the real error.
- `executemany` keeps the DB-API error class of the failing statement
  (`IntegrityError` stays `IntegrityError`). Only non-DB errors are wrapped in
  `ProgrammingError`. The failing index is now in `error.__notes__`
  (`"executemany failed at param set i/n"`), and `str(error)` is unchanged.
  Code that matched the message text must read the note instead.
- `float('nan')`, `float('inf')` and `float('-inf')` parameters are sent as
  `CAST('NaN' AS DOUBLE PRECISION)` and similar, not as bare `nan`/`inf`, which
  Netezza rejects as unknown identifiers. A `Decimal` NaN or infinity raises
  `NotSupportedError`, because NUMERIC has no such values.
- Netezza has no `E'...'` escape strings, so the placeholder scanner no longer
  treats them specially. A backslash before a closing quote no longer hides the
  `?` or `$n` placeholders that follow it.
- A password sent over a plaintext session (`securityLevel` below 2, server
  without TLS) is logged as a warning. It is not blocked, so existing setups keep
  working.
- A value that fails to decode in a large result made the native batch decoder
  rescan the rest of the buffer once per row, so one bad row cost time quadratic in
  the rows before it. The batch now stops in front of that row and each row is
  decoded once. The error is still deferred to `ReadyForQuery` as before.
- While a large result streamed from a fast server, the event loop got no turns
  until the whole result had been decoded. A `cancel()` or timer issued during the
  read (for example after `asyncio.sleep`) was delayed, and the pure-Python decoder
  was affected most. The result reader now yields to the loop at least every 5 ms.
- `CHAR`/`NCHAR` values containing non-Latin-1 text were corrupted by padding:
  the result was built as a 1-byte-kind string, so `ż` came back as `|`. Found on
  a live Netezza 11.2 appliance; the native decoder keeps the text intact.
- Handshake sent `REMOTE_PID` twice on the version-6 path. The option sequence
  now matches the reference driver.
- DBOS `NUMERIC` values with more than 28 significant digits were rounded by the
  decimal context. Values are now built exactly from their unscaled digits and
  scale, in both the native and the pure-Python decoder.
- DBOS empty `VARCHAR` values decode as `""`; they used to come back as `None`.
  Only the presence bitmap produces `NULL`.
- DBOS `SMALLINT` values were read unsigned in the pure-Python decoder, so `-32768`
  came back as `32768`.
- A `DataError` from a value that fails to decode was dropped by `fetchall` and
  `nextset`. Both now raise it when the result reaches `ReadyForQuery`.
- An oversized protocol read length raised a bare `ValueError`; it is now an
  `InterfaceError` that requires reconnecting.
- Undecodable bytes in notices, error text and debug-logged server text no longer
  escape as `UnicodeDecodeError`; they are decoded with replacement characters.
- Handshake: accept only strictly decreasing version downgrades, require exactly
  two-byte MD5/SHA-256 salts, and reject unknown authentication requests before
  reading further.
- A read cancelled or timed out mid-frame now retires the connection instead of
  resynchronizing it. Connections are also closed on any protocol framing fault.
- Value decode errors in result rows raise `DataError` at `ReadyForQuery` and
  leave the session usable. A server error takes precedence over them.
- Harden DBOS row decoding: layout checks run before any value is read, and
  decode failures propagate instead of becoming `None`.
- Pools probe idle sockets on checkout and never return a closed or desynced
  connection to the pool on release.
- `NzPool` no longer holds its condition lock while it opens, validates or closes a
  connection. A slow connect used to stall every other `acquire()` and `release()`.
  `SyncPool` likewise no longer holds its lock during connects, closes or rollbacks.
- The idle-connection sweep owns each connection while it validates it, so
  `acquire()` cannot receive a connection that is being tested. A cancelled sweep
  closes the connection it holds.
- `SyncPool.acquire()` no longer leaks a pool slot when a connect fails. Before, each
  failed connect shrank the pool by one until `acquire()` timed out.
- Two tasks using one connection at the same time now raise `InterfaceError` instead
  of interleaving protocol messages. Use one connection per concurrent task, or a
  pool. Sequential use is unchanged, and `cancel()` and `close()` still run while an
  operation is in flight.
- A client exception raised while `load_data` streams its rows desynchronised the
  protocol (`orphaned response incomplete`). The driver now sends the server an
  error frame, raises the original exception and closes the connection.
- A failed `load_data` no longer leaves an empty table behind when autocommit is
  on, because `CREATE TABLE` and the load share one transaction.
- Inference returned `CLOB` for long text and `BYTEA` for bytes. This server
  rejects both in `CREATE TABLE`, so those loads failed. Text now infers
  `VARCHAR`/`NVARCHAR`, and bytes are refused with a message.
- Decimals in exponent form (`1E+2`) are written in plain digits, which the
  server accepts. Characters that the chosen encoding cannot hold are refused,
  because the server would otherwise store them as mojibake.

## 0.7.3 - 2026-09-30

### Performance

- Pass DBOS payload views directly to the decoders and bound the receive buffer
  pool's retained memory.

## 0.7.1 - 2026-09-27

### Fixed

- Keep external-table file reads on the asyncio worker thread and satisfy the
  regression coverage for that non-blocking path.
- Use a standards-compatible license declaration for package builds.

## 0.7.0 - 2026-09-27

### Added

- Add metadata helpers for functions, constraints, distribution and organize
  keys, detailed object listings, and detailed object search.
- Add DDL reconstruction and batch export for external tables and synonyms.
- Include external tables and synonyms in database DDL exports by default,
  with options to filter or omit each object type.
- Split oversized generated external-table import chunks to the server's
  negotiated block size.
- Add unit and live-database coverage for DDL reconstruction and external-table
  transfer chunking.

## 0.6.0 - 2026-09-23

### Added

- Add catalog-backed DDL generation for tables, views, and stored procedures,
  including single-object helpers, filtered lists, and database/schema exports.
- Add a runnable DDL export example with per-object error reporting and SQL file
  output.

### Performance

- Preserve DBOS row buffers as `memoryview` objects through cursor fetching to
  reduce row-copy allocations.

## 0.5.0 - 2026-09-05

- Propagate commit failures from connection/transaction contexts; begin explicit
  sync transactions and reset transaction state after commit/rollback.
- Reject truncated protocol reads and invalid lengths instead of padding with zeros.
- Pass the TLS server hostname, use buffered handshake I/O and support nonblocking
  TLS transport reads/writes. Remove authentication responses and cancel secrets from logs.
- Export the DB-API module interface from `sync`; provide OID category type objects,
  binary conversion, diagnostic properties and DB-API errors for closed cursors.
- Tighten ODBC comparisons: preserve whitespace, decimal precision and timestamp values.
- Accept `NZ_DEV_DATABASE` with precedence over legacy `NZ_DEV_DB`; skip live tests
  without explicit environment configuration.
- Add offline failure regressions, local TLS tests and temporary-table live regressions.
- Test C and pure-Python modes across supported Python/OS versions; measure branch
  coverage, test installed artifacts outside the checkout and gate publishing on quality checks.

Compatibility: autocommit=True and arraysize=100 remain unchanged. Type categories
are no longer single OID integers; use equality with cursor.description type codes.

## 0.4.1

### Fixed

- Protocol desync after orphaned backend responses: `_drain_socket` now consumes length+payload for notification (`A`) and unknown (`0`) messages, skips null padding, and fails closed with `InterfaceError` if ReadyForQuery is not reached.
- `_execute` drains when unread non-null socket/buffer bytes remain, not only when `_dirty_socket` is set (e.g. orphaned `SELECT CURRENT_SID` before the next statement).

## 0.4.0

### Breaking

- **SSL fail-closed:** connections with `securityLevel >= 2` no longer silently fall back to an unencrypted session when SSL negotiation fails. To restore the previous behaviour, pass `ssl={"ssl_allow_fallback": True}` explicitly.

### Added

- GitHub Actions workflow `.github/workflows/test.yaml` — unit tests, mypy/pyright, wheel/sdist import smoke (no live Netezza).
- Unit tests: `buffer_pool`, `buffered_stream`, `csv_import`, pool acquire behaviour.
- `tests/_helpers.py` — shared ODBC comparison helpers.
- `tools/verify_c_python_parity.py` — wrapper around parity unit tests.
- `nzpy_extended/py.typed` — PEP 561 typing marker.
- `docs/async_api.md`, `CONTRIBUTING.md`.
- `ErrorResponseDict` TypedDict in `exceptions.py`.
- SSL option `ssl_allow_fallback` in `ssl` dict (default `False`).

### Changed

- `NzPool.acquire` — reservation tracking and safe cleanup on validation failure.
- `SyncPool.acquire` / `_maintain_loop` — connection validation outside pool lock.
- Cleanup paths log at DEBUG instead of silently swallowing exceptions.
- `_protocol.py` — `_deliver_notice` helper; removed unused `_dispatch_pg_message`.

### Fixed

- `SyncPool.acquire` — decrement `_created` when pool closes during connection validation (no double-decrement on `RuntimeError`).
- `SyncConnection.__enter__` — restore `with sync_nzpy.connect(...)` context manager support.
- README reference to missing `verify_c_python_parity.py`.
- `test_type_recognition` silent pass replaced with explicit `pytest.skip`.

## 0.3.6

- Extended driver features: metadata API, pools, bulk load, FastAPI helpers.
- C extension with pure-Python fallback for DBOS row parsing.
- Async-first API with sync DB-API 2.0 wrapper.

## 0.3.5

- Production-stable release on PyPI (`nzpy-extended`).
- Wheels for Python 3.12–3.14 on Linux, macOS, and Windows.
