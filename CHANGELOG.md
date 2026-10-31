# Changelog

All notable changes to `nzpy_extended` are documented in this file.

## Unreleased

### Added

- Support Python 3.11 through 3.15. Wheels and CI matrices now cover all five
  versions on Linux, macOS and Windows.
- Offline Netezza wire-protocol mock (`tests/mock_netezza.py`) used by new unit
  tests for the handshake, query phase, pool probing and cancel/timeout recovery,
  with no database required.
- Golden wire fixtures captured from a real appliance (`tests/data/wire`) are
  replayed offline under several fragmentation sizes, and every truncation point
  must fail with a driver error.
- Seeded fuzz and property tests for malformed responses and the native and
  pure-Python DBOS decoders (`tests/test_wire_fuzz_unit.py`).

### Changed

- The DBOS row decoder has a native implementation in Rust (PyO3, `rust/`),
  built with maturin as `nzpy_extended.rust_ext`. It is used by default.
  `NZPY_EXTENDED_NO_NATIVE=1` selects the pure-Python decoder instead. Both
  decoders produce the same results; the pure-Python decoder is the reference.
  Inputs outside the widths a Netezza 11.2 appliance sends (for example a 12-byte
  `NUMERIC` chunk, a `TIMESTAMP` that is not 8 bytes, `CHAR` size 0, or a negative
  4-byte `TIME`) decode as the reference does.
- The environment variable `NZPY_EXTENDED_NO_CEXT` is renamed to
  `NZPY_EXTENDED_NO_NATIVE`. The old name is no longer read.
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

### Fixed

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
