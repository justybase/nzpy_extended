# Contributing to nzpy_extended

Thank you for contributing. This document covers local development, testing, and pull requests.

## Setup

```shell
git clone https://github.com/KrzysztofDusko/nzpy_extended.git
cd nzpy_extended
python -m pip install -e ".[test,lint]"
```

Python **3.12+** and a C compiler are required to build the optional C extension. Set `NZPY_EXTENDED_NO_CEXT=1` to skip the extension during development.

## Running tests

The current offline CI command discovers every test marked `unit`:

```shell
pytest tests -m unit --strict-config --cov=nzpy_extended --cov-branch
NZPY_EXTENDED_NO_CEXT=1 pytest tests -m unit --strict-config
```

Install `.[test]` first; this includes the timeout and coverage plugins. The C
job asserts that the compiled extension is available before testing. CI runs both
implementations on Python 3.12–3.14 and Linux, macOS and Windows.

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
  tests/test_c_python_parity_unit.py \
  tests/test_buffer_pool.py \
  tests/test_buffered_stream.py \
  tests/test_csv_import_unit.py \
  tests/test_pool_unit.py \
  -v
```

Type checks:

```shell
mypy nzpy_extended
pyright nzpy_extended
```

C/Python parity shortcut:

```shell
python tools/verify_c_python_parity.py
```

### Local integration (requires Netezza)

Set environment variables:

```shell
export NZ_DEV_HOST=your_host
export NZ_DEV_PORT=5480
export NZ_DEV_DB=JUST_DATA
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

Releases are tagged `vX.Y.Z` and published via `.github/workflows/publish.yaml` (cibuildwheel + PyPI OIDC).
