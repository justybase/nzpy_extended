# Contributing to nzpy_extended

Thank you for contributing. This document covers local development, testing, and pull requests.

## Setup

```shell
git clone https://github.com/justybase/nzpy_extended.git
cd nzpy_extended
python -m pip install -e ".[test,lint]"
```

Python **3.11+** and a stable **Rust toolchain** (via [rustup](https://rustup.rs)) are required: the native DBOS decoder is built with maturin from `rust/`. Set `NZPY_EXTENDED_NO_NATIVE=1` at runtime to force the pure-Python decoder; it no longer skips a build step.

## Running tests

The current offline CI command discovers every test marked `unit`:

```shell
pytest tests -m unit --strict-config --cov=nzpy_extended --cov-branch
NZPY_EXTENDED_NO_NATIVE=1 pytest tests -m unit --strict-config
```

Install `.[test]` first; this includes the timeout and coverage plugins. The unit
job asserts that the native extension (`rust_ext`) is loaded unless
`NZPY_EXTENDED_NO_NATIVE=1` is set. Each push runs a reduced set of combinations;
a weekly run covers both implementations on Python 3.11–3.15 and Linux, macOS and
Windows.

### Rust crate

The native decoder lives in `rust/` (crate `nzpy_rust_ext`, module `nzpy_extended.rust_ext`).
Run its checks from that directory:

```shell
cd rust
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --lib
```

Fuzz the framing and layout code with [cargo-fuzz](https://rust-fuzz.github.io/book/cargo-fuzz.html)
(needs `cargo install cargo-fuzz` and a nightly toolchain). Run from `rust/`:

```shell
cargo +nightly fuzz run fuzz_frames -- -max_total_time=60
cargo +nightly fuzz run fuzz_layout -- -max_total_time=60
```

Crashing inputs are written to `rust/fuzz/artifacts/`, which is not committed. Turn a
crash into a `cargo test` regression in `rust/src/` before fixing it.

`cargo test` covers the pure-Rust logic (layout, framing, numeric, temporal).
Python-level behaviour is covered by the parity tests in `tests/test_native_parity_unit.py`.

Live tests require explicit credentials and a database in the environment.
`NZ_DEV_DATABASE` takes precedence over the legacy `NZ_DEV_DB` alias. Without
configuration live tests are skipped; do not rely on source-code defaults.
The focused `tests/test_driver_contract_integration.py` regressions only create
unique temporary tables. Older full-suite tests can modify permanent test objects
and must run in a dedicated disposable test database.

Reference-driver parity requires exact integer, Decimal, text and deterministic
temporal values. Floating-point comparisons alone have an explicit tolerance.
Represent known reference-driver limitations as narrowly scoped skips, never
global truncation/rounding allowances. Local TLS tests generate temporary
certificates using the `openssl` executable and skip if it is unavailable.

### CI profile (no database)

These tests run on every GitHub Actions push/PR. **No live Netezza instance is available in CI.**

```shell
pytest tests/test_paramstyle.py \
  tests/test_typeobjects.py \
  tests/test_regressions_unit.py \
  tests/test_native_parity_unit.py \
  tests/test_buffer_pool.py \
  tests/test_buffered_stream.py \
  tests/test_csv_import_unit.py \
  tests/test_pool_unit.py \
  -v
```

Type checks:

```shell
mypy nzpy_extended
pyright --pythonpath "$(python -c 'import sys; print(sys.executable)')" nzpy_extended
```

Native/Python parity shortcut:

```shell
python tools/verify_native_parity.py
```

### Local integration (requires Netezza)

Set environment variables:

```shell
export NZ_DEV_HOST=your_host
export NZ_DEV_PORT=5480
export NZ_DEV_DATABASE=JUST_DATA
export NZ_DEV_USER=admin
export NZ_DEV_PASSWORD=password
```

Profiles:

| Profile | Command |
|---------|---------|
| Smoke | `pytest tests/ -m smoke -v` |
| Full | `pytest tests/ -m full -v` |
| Unit only | `pytest tests/ -m unit -v` |
| Benchmark | `pytest tests/ -m benchmark -v` |
| JustyBase marathon | `pytest tests/test_odbc_comparison_node.py -m reference_node -v` |

Run the complete suite locally:

```shell
pytest tests/ -v
```

Live native-versus-Python timing (creates and drops its own table; disposable database only):

```shell
python tools/examples/benchmark_native_vs_python.py --output native_vs_python.json
```

## Pytest markers

Defined in `pytest.ini`:

- `unit` — no database required
- `smoke` — quick integration checks
- `full` — comprehensive integration
- `benchmark` — performance tests
- `reference_node` — large parity corpus against the independent JustyBase Node driver (~727 queries)

## Pull requests

1. Run the **CI profile** tests and type checks locally before opening a PR.
2. Run integration tests against Netezza if your change touches protocol, types, or SQL behaviour.
3. Keep changes focused; match existing code style.
4. Do not commit virtualenvs or build artifacts.

## Release

Releases are tagged `vX.Y.Z` and published via `.github/workflows/publish.yaml`
(cibuildwheel driving the maturin build backend, with a Rust toolchain installed on
each platform, then PyPI OIDC). The version is read from `rust/Cargo.toml`, so bump
it there together with the tag. Source distributions need a Rust toolchain to install.

The tag publishes to PyPI and then creates the GitHub Release from the matching
`CHANGELOG.md` section (`## X.Y.Z - date`), so add that section before tagging. To
test the builds without publishing, run the workflow manually with `publish` off.
