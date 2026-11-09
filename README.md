# nzpy_extended

[![PyPI version](https://img.shields.io/pypi/v/nzpy-extended)](https://pypi.org/project/nzpy-extended/)
[![Python versions](https://img.shields.io/pypi/pyversions/nzpy-extended)](https://pypi.org/project/nzpy-extended/)
[![CI](https://github.com/justybase/nzpy_extended/actions/workflows/test.yaml/badge.svg?branch=main)](https://github.com/justybase/nzpy_extended/actions/workflows/test.yaml)
[![License](https://img.shields.io/pypi/l/nzpy-extended)](LICENSE)

**A Python driver for IBM Netezza Performance Server (NPS).** `nzpy_extended`
is a maintained fork of IBM's [`nzpy`](https://github.com/IBM/nzpy), with
asynchronous and synchronous APIs, an optional Rust decoder, connection pools,
and utilities for bulk loading and catalog introspection.

The package is published on [PyPI](https://pypi.org/project/nzpy-extended/).
Install it with:

```shell
python -m pip install nzpy-extended
```

## Highlights

- **Async and sync APIs.** Use `nzpy_extended` with `asyncio`, or
  `nzpy_extended.sync` for blocking applications.
- **DB-API 2.0-oriented sync interface.** Supports standard cursor operations,
  parameter placeholders, transactions, and the DB-API exception hierarchy.
  See the [compliance notes](docs/pep249_compliance.md) for deviations.
- **Native result decoding.** A Rust extension accelerates decoding Netezza
  result rows. If it is unavailable, the driver can fall back to the
  pure-Python decoder.
- **Connection pools.** Async and sync reusable pools, plus null-pool variants.
- **Bulk loading.** Stream Python iterables or local CSV files using Netezza's
  external-table protocol.
- **Catalog metadata.** Async helpers for tables, columns, views, procedures,
  distribution keys, and other system catalog information.
- **Timeouts and cancellation.** Configure connection and command timeouts and
  cancel running queries.

## Compatibility

| Runtime or platform | Support |
|---|---|
| CPython | Python 3.11–3.15 |
| Linux | x86_64 and aarch64; manylinux and musllinux wheels |
| macOS | arm64 and universal2 wheels |
| Windows | x86_64 and ARM64 wheels |

Published wheels use the CPython stable ABI and support Python 3.11 and newer.
Free-threaded CPython builds are not covered by these wheels. PyPy is not a
tested or supported runtime.

On supported platforms, pip installs a prebuilt wheel. On other platforms, pip
may build the native extension from source; this requires a stable Rust
toolchain. The pure-Python decoder is available as a runtime fallback, but does
not remove the Rust requirement for building a source distribution.

## Quick start

The examples read connection settings from environment variables so credentials
do not need to be written into source code.

### Synchronous

```python
import os
import nzpy_extended.sync as nzpy

conn = nzpy.connect(
    host=os.environ["NZ_HOST"],
    port=int(os.environ.get("NZ_PORT", "5480")),
    database=os.environ["NZ_DATABASE"],
    user=os.environ["NZ_USER"],
    password=os.environ["NZ_PASSWORD"],
)

try:
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        print(cur.fetchone())
finally:
    conn.close()
```

### Asynchronous

```python
import asyncio
import os
import nzpy_extended as nzpy

async def main():
    async with await nzpy.connect(
        host=os.environ["NZ_HOST"],
        port=int(os.environ.get("NZ_PORT", "5480")),
        database=os.environ["NZ_DATABASE"],
        user=os.environ["NZ_USER"],
        password=os.environ["NZ_PASSWORD"],
    ) as conn:
        cur = conn.cursor()
        await cur.execute("SELECT 1")
        print(await cur.fetchone())

asyncio.run(main())
```

Use `?` placeholders for values rather than interpolating them into SQL:

```python
cur.execute("SELECT id, name FROM users WHERE id = ?", (user_id,))
```

Parameters are rendered as SQL literals by the client; they are not server-side
prepared-statement bindings. Identifiers such as table and column names cannot
be supplied as value parameters.

## Common operations

### Transactions

Autocommit is enabled by default for compatibility with the upstream driver.
For explicit synchronous transactions, use `conn.transaction()` or set
`conn.autocommit = False` and call `commit()` or `rollback()`.

```python
with conn.transaction():
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE accounts SET status = ? WHERE id = ?",
            ("active", 42),
        )
```

The async API provides `commit()` and `rollback()` on the connection. For
transaction semantics and DB-API differences, see the
[PEP 249 notes](docs/pep249_compliance.md).

### Bulk loading

Use `load_data()` for iterable or async-iterable rows, and `load_csv()` for
streaming a local CSV file. Both APIs are available in synchronous and
asynchronous forms.

```python
count = conn.load_data(
    "staging_items",
    [(1, "alpha"), (2, "beta")],
    columns=[("id", "INTEGER"), ("name", "VARCHAR(100)")],
)
print(f"Loaded {count} rows")
```

See the [bulk loading guide](docs/load_data.md) for schema inference, encoding,
validation, streaming behavior, and error handling.

### Pools, timeouts, and metadata

- [Connection pools](docs/pool.md) — async and sync pool APIs and lifecycle.
- [Timeouts and cancellation](docs/timeout_and_cancel.md) — connection and
  command timeout behavior.
- [Catalog metadata](docs/metadata_api.md) — async catalog inspection helpers.

## Documentation

| Guide | Contents |
|---|---|
| [Async API](docs/async_api.md) | Async connection, cursor, SSL, and API limitations |
| [Sync API](docs/sync_api.md) | Synchronous DB-API connection and cursor reference |
| [PEP 249 compliance](docs/pep249_compliance.md) | Supported behavior and known deviations |
| [Connection pools](docs/pool.md) | Pool types, configuration, and lifecycle |
| [Timeouts and cancellation](docs/timeout_and_cancel.md) | Timeout scopes, cancellation, and session behavior |
| [Bulk data loading](docs/load_data.md) | `load_data()` API and value/encoding rules |
| [Metadata API](docs/metadata_api.md) | Netezza catalog introspection |
| [Benchmarks](docs/benchmarks.md) | Measurements, methodology, and reproduction |
| [Changelog](CHANGELOG.md) | Release notes and upgrade-impacting changes |

Runnable examples are in [`examples/`](examples/). For questions and bug
reports, use the [issue tracker](https://github.com/justybase/nzpy_extended/issues).

## Performance

The Rust decoder can substantially improve row-decoding throughput, but
end-to-end performance depends on the query, data types, server, network, and
client hardware. See the [benchmark results and methodology](docs/benchmarks.md)
before comparing measurements; they are workload-specific and are not a
throughput guarantee.

To deliberately use the pure-Python decoder:

```shell
NZPY_EXTENDED_NO_NATIVE=1 python your_script.py
```

If the native module cannot be imported, the driver emits a `RuntimeWarning`
and falls back to pure Python. Setting `NZPY_EXTENDED_NO_NATIVE=1` explicitly
selects that fallback without the warning. The former
`NZPY_EXTENDED_NO_CEXT` setting was removed in version 0.9.

## Cross-driver conformance tests

The shared corpus lives in `conformance/` at the workspace root and will move
to `tests/conformance` as a submodule. It provisions its own `CONFORMANCE`
schema on a live appliance and runs the same case corpus against Python, Node,
C# and Rust.

```powershell
..\conformance\run-all.ps1 -Profile smoke -Bootstrap
```

The Python runner is `tools/conformance_runner.py`. It uses the async API and
emits the canonical JSONL from `conformance/runner-contract.md`. Host and
credentials come only from `NZ_DEV_HOST` and the other `NZ_DEV_*` variables;
`tests/conftest.py` no longer contains a code password default.

## Testing and development

Install the test and lint dependencies, then run the database-free unit tests:

```shell
python -m pip install -e ".[test,lint]"
pytest tests -m unit -v --strict-config
NZPY_EXTENDED_NO_NATIVE=1 pytest tests -m unit -v --strict-config
mypy nzpy_extended
pyright nzpy_extended
python tools/verify_native_parity.py
```

Live integration profiles require a disposable Netezza database and the
`NZ_DEV_HOST`, `NZ_DEV_PORT`, `NZ_DEV_DATABASE`, `NZ_DEV_USER`, and
`NZ_DEV_PASSWORD` environment variables. `NZ_DEV_DB` is accepted as a legacy
alternative to `NZ_DEV_DATABASE`. Run the profiles with:

```shell
pytest tests -m smoke -v
pytest tests -m full -v
```

Do not run live tests against a production database. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the development workflow and Rust
checks.

## Upgrading

Version 0.9.0 includes behavior changes that may affect applications: server
errors now use more specific DB-API exception classes, binary and geometry
values are returned as `bytes`, invalid parameter counts and NUL characters
raise errors, and fetching discarded cursor results raises `InterfaceError`.
It also changes logging defaults and tightens validation of paths used by the
external-table protocol.

Review the [changelog](CHANGELOG.md), especially the 0.9.0 entry, before
upgrading from an earlier release. Version 0.9.1 changes packaging and release
automation, not driver behavior.

## License

Apache License 2.0. See [LICENSE](LICENSE).
