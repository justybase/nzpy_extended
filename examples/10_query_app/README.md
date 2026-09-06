# Netezza SQL Workspace — FastAPI example

Modular SQL editor example for `nzpy_extended`, inspired by the architecture of the JustyBase web application.

## Features

- multiple SQL tabs with separate Monaco models;
- execution of the statement under the cursor, the selected text, or the entire script;
- parallel queries with independent cancel/progress handling over WebSocket;
- a separate result tab for each statement;
- server-side SQLite result sessions with TTL, restore after reload, and paging;
- a Tabulator-based virtualized grid with server-side filtering/sorting, drag-and-drop multi-column grouping, column resizing, cell-range selection, clipboard support, a context menu, and a result search field;
- a lazy schema tree: database → schema → object type → object → columns;
- Netezza completion powered by a backend metadata cache;
- basic SQL diagnostics and DDL/DML operation previews;
- frontend CSV/XLSX/XLSB export powered by `@justybase/spreadsheet-tasks`, plus backwards-compatible `/api/query` and `/api/cancel` endpoints.

## Getting started

Wymagane są `NZ_DEV_HOST`, `NZ_DEV_PORT`, `NZ_DEV_DATABASE`, `NZ_DEV_USER` i `NZ_DEV_PASSWORD`.

```bash
cd examples/10_query_app
python3 -m pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
python3 server.py
```

The application will be available at `http://127.0.0.1:8480`.

Result sessions are stored in `/tmp/nzpy_extended-query-sessions` by default.
The following settings can be used to change the defaults:

```text
NZ_RESULT_STORAGE_DIR
NZ_RESULT_SESSION_TTL
NZ_RESULT_ROW_LIMIT
NZ_RESULT_PAGE_SIZE
NZ_RESULT_CHUNK_SIZE
NZ_QUERY_TIMEOUT
```

## Tests

```bash
cd examples/10_query_app
python3 -m pytest tests -q
cd frontend
npm run build
```

Playwright/Netezza E2E tests should use an isolated object created from `NZ_DEV_*` and clean it up after the test.

Run the full browser suite after building the frontend. It requires a Playwright browser:

```bash
cd frontend
npx playwright install chromium
NZ_E2E=1 NZ_E2E_START_SERVER=1 npm run test:e2e
```

Before running the suite, install the Python dependencies with `python3 -m pip install -r ../requirements.txt`; the `uvicorn[standard]` extra enables streaming, cancel, and progress over WebSocket. If this backend is unavailable, the editor uses a synchronous HTTP fallback for basic execution and the grid. If you use a virtual environment, set its interpreter with `NZ_E2E_PYTHON=/path/to/venv/bin/python`. `python-multipart` is also required by the legacy form-based import/export endpoints.

Global setup creates an `NZPY_E2E_*` table, tests completion, multiple tabs, and result rendering; teardown removes only the object created by the test.

## Main API contracts

```text
WS  /api/v1/workspace/ws
POST /api/v1/query/preview
POST /api/v1/query/execute
GET  /api/v1/schema/tree
POST /api/v1/language/completion
POST /api/v1/language/diagnostics
GET  /api/v1/results/{session_id}
POST /api/v1/results/{session_id}/page
POST /api/v1/results/{session_id}/export
```

Results are not sent as one large JSON payload. The executor stores them in chunks, and the browser fetches only the page it needs.
