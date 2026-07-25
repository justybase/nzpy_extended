# Changelog

All notable changes to `nzpy_extended` are documented in this file.

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
