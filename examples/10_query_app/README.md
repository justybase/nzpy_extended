# Netezza SQL Workspace — FastAPI example

Modułowy przykład edytora SQL dla `nzpy_extended`, inspirowany architekturą webowego JustyBase.

## Funkcje

- wiele zakładek SQL z osobnymi modelami Monaco;
- wykonywanie statementu pod kursorem, zaznaczenia albo całego skryptu;
- równoległe zapytania z niezależnym cancel/progress przez WebSocket;
- osobne result-tab dla każdego statementu;
- serwerowe sesje wyników SQLite z TTL, restore po reloadzie i pagingiem;
- wirtualizowany grid z filtrowaniem, sortowaniem, resize i menu kontekstowym;
- lazy schema tree: database → schema → typ obiektu → obiekt → kolumny;
- completion Netezza oparty o backendowy metadata cache;
- podstawowa diagnostyka SQL i preview operacji DDL/DML;
- eksport wyników oraz kompatybilne stare endpointy `/api/query` i `/api/cancel`.

## Uruchomienie

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

Aplikacja będzie dostępna pod `http://127.0.0.1:8480`.

Sesje wyników są przechowywane domyślnie w `/tmp/nzpy_extended-query-sessions`.
Można zmienić ustawienia przez:

```text
NZ_RESULT_STORAGE_DIR
NZ_RESULT_SESSION_TTL
NZ_RESULT_ROW_LIMIT
NZ_RESULT_PAGE_SIZE
NZ_RESULT_CHUNK_SIZE
NZ_QUERY_TIMEOUT
```

## Testy

```bash
cd examples/10_query_app
python3 -m pytest tests -q
cd frontend
npm run build
```

Playwright/Netezza E2E powinien korzystać z izolowanego obiektu utworzonego z `NZ_DEV_*` i sprzątać go po teście.

Pełny browser suite uruchamia się po zbudowaniu frontendu i wymaga przeglądarki Playwright:

```bash
cd frontend
npx playwright install chromium
NZ_E2E=1 NZ_E2E_START_SERVER=1 npm run test:e2e
```

Przed uruchomieniem doinstaluj zależności Pythona przez `python3 -m pip install -r ../requirements.txt`; extra `uvicorn[standard]` dostarcza backend WebSocket wymagany przez edytor. Jeśli używasz virtualenvu, wskaż jego interpreter przez `NZ_E2E_PYTHON=/ścieżka/venv/bin/python`. `python-multipart` jest potrzebny również dla legacy endpointów formularzowych import/export.

Global setup tworzy tabelę `NZPY_E2E_*`, testuje completion, wiele zakładek i renderowanie wyniku, a teardown usuwa wyłącznie utworzony obiekt.

## Główne kontrakty

```text
WS  /api/v1/workspace/ws
POST /api/v1/query/preview
GET  /api/v1/schema/tree
POST /api/v1/language/completion
POST /api/v1/language/diagnostics
GET  /api/v1/results/{session_id}
POST /api/v1/results/{session_id}/page
POST /api/v1/results/{session_id}/export
```

Wyniki nie są przesyłane jako jeden duży JSON. Executor zapisuje je partiami, a przeglądarka pobiera wyłącznie potrzebną stronę.
