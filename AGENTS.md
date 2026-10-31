# Repository Guidelines

## Project Structure & Module Organization

- `nzpy_extended/` contains the driver: async and sync APIs, protocol handling, serialization, types, pooling, metadata, and the native DBOS decoder (`rust_ext`).
- `rust/` contains the Rust crate (PyO3, built by maturin) that implements the native decoder: `rust/src/` (`lib.rs`, `layout.rs`, `frames.rs`, `numeric.rs`, `temporal.rs`, `fields.rs`, `pycache.rs`, `nztype.rs`).
- `tests/` contains pytest unit, integration, parity, regression, and benchmark tests; reusable fixtures and helpers live beside them, with data files under `tests/data/` and `tests/test_data/`.
- `examples/` contains runnable API examples and larger applications such as `10_query_app/` and `11_mis_dashboard/`. Reference documentation is in `docs/`; development utilities are in `tools/`.
- `pyproject.toml` defines packaging (maturin build backend; the version comes from `rust/Cargo.toml`), dependencies, and strict type-checking configuration.

## Build, Test, and Development Commands

Use Python 3.11+ (3.11 through 3.15 are supported) and a stable Rust toolchain (maturin builds the native decoder), then install editable test and lint dependencies:

```shell
python -m pip install -e ".[test,lint]"
pytest tests -m unit -v --strict-config --cov=nzpy_extended --cov-branch
NZPY_EXTENDED_NO_NATIVE=1 pytest tests -m unit -v --strict-config
mypy nzpy_extended
pyright nzpy_extended
python tools/verify_native_parity.py
cd rust && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test --lib
```

Set `NZPY_EXTENDED_NO_NATIVE=1` at runtime to force the pure-Python decoder. Integration profiles require `NZ_DEV_HOST`, `NZ_DEV_PORT`, `NZ_DEV_DATABASE` (preferred over legacy `NZ_DEV_DB`), `NZ_DEV_USER`, and `NZ_DEV_PASSWORD`; run them with `pytest tests -m smoke -v` or `pytest tests -m full -v`. Build distributions with `python -m build --wheel` or `python -m build --sdist` after installing `build`.

## Coding Style & Naming Conventions

Use four-space indentation, clear type annotations, and the existing Python style. Name modules, functions, and variables with `snake_case`, classes with `CapWords`, and constants with `UPPER_SNAKE_CASE`. Keep async/sync behavior and native-extension and Python implementations semantically aligned. No separate formatter is configured; use mypy and pyright as the required static checks.

## Testing Guidelines

Name test files `test_*.py` and test functions `test_*`. Use `unit` for database-free tests, `smoke` for quick integration checks, `full` for comprehensive integration, `benchmark` for performance tests, and `reference_node` for extended parity tests. CI collects branch coverage but does not define a fixed threshold. Run live tests only against a disposable database; never depend on source-code credential defaults.

## Commit & Pull Request Guidelines

Use concise, imperative commit subjects. Existing history commonly uses prefixes such as `feat:`, `fix:`, and `refactor:` alongside clear descriptive subjects. Keep pull requests focused and include a summary, tests and type-check results, relevant issue links, and screenshots for UI/example changes. Do not commit virtual environments, build artifacts, credentials, or generated secrets.
