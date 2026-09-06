# Sales Decision Cockpit — exception-first reporting example

This is an independent FastAPI + `nzpy_extended` example for a sales network.
It deliberately uses a different reporting pattern from
`examples/11_mis_dashboard`.

The previous example is a classic MIS catalogue: KPI cards, monthly trends,
breakdowns, drill-downs and a large detail ledger. This example is an
operating-review cockpit:

1. **Control tower** starts with the sales units that need attention.
2. **Driver analysis** explains the selected gap with an additive bridge.
3. **Review pack** turns the evidence into a one-page, print-ready discussion.

The interface and seed data are in English, while this README is also kept in
English to match the surrounding examples.

## What this example demonstrates

- weekly rather than monthly reporting;
- closed-period reporting to avoid mixing partial and complete periods;
- lagging measures (sales, target, margin) beside leading indicators (leads,
  conversion, cancellations and weighted pipeline);
- explainable `critical`, `warning` and `good` thresholds;
- an additive actual-minus-target bridge by product, channel, region or unit;
- deterministic narrative recommendations that are read-only and do not
  pretend to be a workflow system;
- a bounded process-local cache and database-free service tests;
- XLSX/XLSB exports built from the same service payload as the UI.

## Reporting philosophy

The main question is not “what are all the numbers?” but “where should the
next management conversation start, and what evidence should be brought to it?”

The control tower groups rows by sales unit (or by seller when a unit is
selected) and sorts them by urgency. A row is `critical` when the four-week
pace is below 90% of target or weighted pipeline cover is below 80%. It is
`warning` when pace is below 100% or cover is below 100%. Everything else is
`good`. The thresholds are deliberately simple and visible so a business user
can challenge them.

The driver bridge is additive. Because the target table has the same
week/seller/product/channel grain as the scorecard, every driver is:

```text
contribution = actual - target
sum(contribution) = selected_scope_actual - selected_scope_target
```

That makes the explanation auditable. The “next action” text is a deterministic
playbook suggestion, not an AI-generated claim and not a persisted task.

## Important async boundary warning

`async def` only describes how a function may suspend; it does **not** turn
synchronous work into non-blocking work. A synchronous file operation, Excel
writer, database call, `time.sleep()` or large in-memory aggregation inside an
`async def` route or service blocks the event loop and delays every concurrent
request handled by that process.

Await the asynchronous Netezza operations. For unavoidable synchronous I/O or
CPU-heavy work, use a bounded worker pool (the export writer in this example
uses `asyncio.to_thread`), or push the aggregation into the database. A minimal
pattern is:

```python
async def make_export() -> str:
    return await asyncio.to_thread(write_workbook_sync)
```

Do not replace `time.sleep()` with an `async def` wrapper; use
`await asyncio.sleep(...)`. Keep created background tasks, cancel and await
them at shutdown, and never assume that declaring an endpoint `async def`
protects it from blocking code. See the full checklist in
[`docs/async-boundaries.md`](docs/async-boundaries.md).

## Setup

Use Python 3.12+ and install the example dependencies:

```bash
python -m pip install -r requirements.txt
```

The seed uses the same connection variables as the driver examples:

```bash
export NZ_DEV_HOST=<database-host>
export NZ_DEV_PORT=<database-port>
export NZ_DEV_DATABASE=<disposable-database>
export NZ_DEV_USER=<database-user>
export NZ_DEV_PASSWORD=<database-password>
```

Create only the new `SDC_*` tables:

```bash
python seed.py                 # normal deterministic dataset
python seed.py --scale 0.25    # smaller disposable dataset
```

Start the dashboard on port 8482:

```bash
python main.py
```

Open <http://localhost:8482>.

Run the database-free tests:

```bash
python -m pytest tests/ -q
```

Optional browser checks require Playwright and a running server:

```bash
pip install playwright
python -m playwright install chromium
python tools/visual_check.py
```

## Architecture

```text
main.py                         FastAPI app factory and cache wiring
app/
├── api/routes/                 thin HTTP endpoints
├── repositories/               SDC repository contract + Netezza cache
├── services/cockpit_service.py decision logic, bridge and narratives
├── services/export_service.py  XLSX/XLSB writers
├── schemas/cockpit.py          public API response models
└── core/                       settings and logging
static/                         vanilla HTML/CSS/JS frontend
sql/                            production-style weekly mart reference
tests/                          service, schema, API and export tests
```

The service reads through `SDCRepository`, so all report calculations can be
tested with an in-memory fake. Netezza is loaded through a bounded LRU cache;
the UI does not query the database for every click.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/meta` | available weeks, regions, units, metrics and cache status |
| `GET /api/cockpit?week=&lookback=&scope=&region=&unit=&metric=` | exception board, headlines, narrative and trend charts |
| `GET /api/drivers?week=&lookback=&scope=&region=&unit=&metric=&dimension=` | additive gap bridge and driver evidence |
| `GET /api/review-pack?week=&scope=&region=&unit=&metric=` | one-page decision pack payload |
| `POST /api/refresh` | reload the complete SDC cache and clear derived payloads |
| `GET /api/export/cockpit/{xlsx\|xlsb}` | exception table export |
| `GET /api/export/drivers/{xlsx\|xlsb}` | driver evidence export |
| `GET /api/status` | database, pool and cache status |

Supported metrics are `sales_amount`, `sales_count` and `margin_amount`.
Supported driver dimensions are `product`, `channel`, `region` and `unit`.
`scope=unit` requires a `unit` code and changes the exception rows to sellers.

## Data model

The schema is intentionally separate from the older `MIS_*` schema. See
[`docs/data_dictionary.md`](docs/data_dictionary.md) for the grain and field
definitions.

- `SDC_DIM_DATE`, `SDC_DIM_REGION`, `SDC_DIM_UNIT`, `SDC_DIM_SELLER`,
  `SDC_DIM_PRODUCT`, `SDC_DIM_CHANNEL` — dimensions;
- `SDC_FACT_WEEKLY_SCORECARD` — actual sales and leading indicators at
  week/seller/product/channel grain;
- `SDC_FACT_WEEKLY_TARGET` — target measures at the same grain;
- `SDC_AUDIT_DATASET_LOAD` — weekly reconciliation evidence;
- `SDC_CONTROL_DATASET_LOAD` — published dataset version and watermark.

`sql/sales_cockpit_mart.sql` shows how a production ETL could build the two
weekly facts from source activity and target tables before publishing them.

## Production boundary

This is a reporting-design example, not a production approval. Authentication,
row-level access control, durable cache generations, SCD2 attribution and a
transactional action workflow are intentionally not duplicated here; those
concerns are demonstrated in `11_mis_dashboard`. A production adaptation must
add them according to the organization’s security and operating model.
