# MIS Dashboard — retail sales network (FastAPI + Netezza example)

A complete example of an MIS-style (Management Information System) reporting
dashboard for a bank's retail sales network. Built with **FastAPI**,
**nzpy_extended**, **cachetools (TTLCache)** and **xlspy** (XLSB/XLSX export).

Everything is in English: table names, columns, menu items, labels and the
sample data (Irish cities and names).

## Architecture (professional FastAPI layout)

The app follows the layered structure used in real FastAPI projects — thin
routes, business logic in services, data access behind a repository interface:

```
main.py                        app factory + lifespan wiring
app/
├── api/
│   ├── deps.py                dependency injection (services from app.state)
│   └── routes/                thin HTTP layer
│       ├── pages.py           GET / (frontend)
│       ├── meta.py            GET /api/meta
│       ├── reports.py         GET /api/report/{id}
│       ├── drill.py           GET /api/drill/{id}/{target} (row drill-down)
│       ├── exports.py         GET /api/export/... (xlsx/xlsb)
│       ├── cache.py           POST /api/cache/refresh
│       └── status.py          GET /api/status
├── services/                  business logic
│   ├── reporting.py           report definitions + pure-Python aggregations
│   ├── report_service.py      orchestration + report payload TTLCache
│   ├── ledger_service.py      sales ledger: server-side pagination/filtering
│   ├── people_service.py      advisor panels + cumulative personal views
│   └── export_service.py      xlspy workbook generation
├── repositories/              data access
│   ├── base.py                MISRepository interface (ABC)
│   └── cached.py              full MIS_* tables in a TTLCache
├── schemas/                   Pydantic response models
├── core/                      settings (env-driven) + logging
└── db/                        Netezza connection pool factory
```

Services depend only on the `MISRepository` interface, so the storage backend
can be swapped — the unit tests use a tiny in-memory `FakeRepository` and
never touch a database (`tests/test_report_service.py`).

## Features

| Area | Reports |
|---|---|
| Sales | Loans, Investments, Insurance, Current accounts (ROR), Savings accounts, Term deposits |
| Balances | ROR balances, savings/deposit/portfolio balances (month-end snapshots) |
| Clients | Acquisition & churn, product penetration (cross-sell) |
| Marketing | Campaign effectiveness (reach, response, conversion, ROI) |
| Sales force | Advisor performance panels (person picker + vertical profile, scores, ratings) |
| Personal | **My branch** (branch manager) and **My results** (advisor) — cumulative sales within the month vs plan and vs previous month |
| Detail | **Sales ledger** — the full sales list with server-side pagination, free-text search, filters and sorting |
| Overview | KPI cards + charts across the whole network |

Each report page offers:

- **Synthetic summary** — monthly aggregates with MoM (month-over-month) deltas
- **Analytic breakdown** — drill-down by branch / region / advisor / product / channel / campaign
- **Row-level drill-through** — click any row of the analytic table to dig in:
  region → its branches, branch → its advisors, advisor → its individual sales
  (each drill view is also downloadable as XLSX/XLSB)
- **Charts** — trends, shares (doughnut), regional comparisons, top-N rankings (Chart.js)
- **Downloads** — every table as **XLSX or XLSB** (generated on the backend with
  `xlspy`, including a hidden "Report info" sheet), and every chart as **PNG or
  PDF** (generated client-side straight from the rendered Chart.js canvas,
  including an "All charts (PDF)" pack in the toolbar)

### Sales ledger — very large analytics with pagination

`Sales ledger` (menu → Detail) pages through the **full sales list** (192k+
rows) with server-side pagination, free-text search (customer, branch,
advisor, product, channel, status), exact filters (product group, channel,
status) and column sorting. The backend joins the cached tables once (keyed on
the cache snapshot) and filters/sorts/paginates in memory — a page loads in
~40 ms after the first join. The filtered set (up to 100k rows) is
downloadable as XLSX/XLSB.

### Advisor performance panels

`Advisor performance` (menu → Sales force) offers a person picker with an
"active only" toggle and a vertical layout per advisor: profile card (avatar
initials, role, branch/region/city, hire date, tenure, status), a rating badge
(1–5), score cards (sales, conversion, quality, activity), plan-attainment
and score trend charts, and a monthly ratings table (plan vs achieved,
attendance %, rating, note) — downloadable as XLSX/XLSB. Data comes from the
new `MIS_FACT_ADVISOR_PERF` table.

### My branch / My results — cumulative within the month

Two role-oriented views (menu → My views) for a branch manager and an
individual advisor. Pick your branch/advisor and a month: day-by-day
**cumulative** sales within the month, a prorated cumulative **plan** line
(plan × day / days-in-month) and the **previous month's** cumulative at the
same point — as KPIs (MTD, plan, attainment %, vs previous month), a three-
series line chart and a day table. Plans come from `MIS_FACT_BRANCH_PLAN`
(branch plan = sum of its advisors' plans) and `MIS_FACT_ADVISOR_PERF`.

## Caching — why Netezza is barely queried

The user-facing requirement: *do not hit Netezza on every click*. This app
implements a two-level cache:

1. **Table cache** (`cachetools.TTLCache`, TTL 15 min by default) — the full
   MIS_* tables (sales list, balance snapshots, client movement, campaign
   results + all dimensions) are loaded **completely into memory** at startup
   and kept warm by a background refresh loop. All report computations run in
   pure Python over these in-memory rows.
2. **Report cache** (`TTLCache`, TTL 2 min) — computed payloads
   (KPIs + tables + charts) are cached per (report, period, dimension), so
   repeated clicks are instant.

Netezza is contacted only when: the table cache is cold, an entry expires, or
you click **Reload data** in the sidebar (`POST /api/cache/refresh`). If a background
refresh fails, the old (stale) data keeps serving — a DB hiccup never empties
the cache. Cache hit/miss counters and the last load time are shown in the
sidebar.

Tunables (environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `NZ_CACHE_TTL_TABLES` | `900` | table cache TTL in seconds |
| `NZ_CACHE_TTL_REPORTS` | `120` | report payload cache TTL in seconds |
| `NZ_CACHE_REFRESH_SECONDS` | `300` | background warm-refresh interval |

## Setup

Connection uses the standard `NZ_DEV_*` variables (same as the driver tests):

```bash
export NZ_DEV_HOST=192.168.0.144
export NZ_DEV_PORT=5480
export NZ_DEV_DATABASE=JUST_DATA
export NZ_DEV_USER=admin
export NZ_DEV_PASSWORD=password
```

Install dependencies:

```bash
pip install fastapi uvicorn cachetools xlspy
```

Create and seed the tables in JUST_DATA (drops and recreates all `MIS_*` tables):

```bash
python seed.py            # full dataset (~190k sales rows, ~20 s)
python seed.py --rows 10  # scaled-down dataset for quick tests
```

Start the dashboard:

```bash
python main.py            # http://0.0.0.0:8481
```

Open <http://localhost:8481>.

Run the unit tests (no database needed — fake repository):

```bash
python -m pytest tests/ -q
```

## Data model (JUST_DATA)

Star schema, all names in English:

- `MIS_DIM_REGION` / `MIS_DIM_BRANCH` / `MIS_DIM_ADVISOR` — the sales network
  (6 regions, 41 branches across Ireland, ~260 advisors)
- `MIS_DIM_PRODUCT` — 21 products in 6 groups: Loans, Investments, Insurance,
  Current accounts (ROR), Savings accounts, Term deposits
- `MIS_DIM_CHANNEL` — Branch, Mobile app, Internet banking, Call center,
  Telemarketing, External agent
- `MIS_DIM_CAMPAIGN` — 40 marketing campaigns
- `MIS_FACT_SALES` — the sales list (booked/cancelled, amount, commission,
  advisor, channel, anonymised customer)
- `MIS_FACT_BALANCES` — month-end balance snapshots per branch/product
- `MIS_FACT_CUSTOMER_MOVEMENT` — monthly acquisition / churn / penetration
- `MIS_FACT_CAMPAIGN_RESULTS` — campaign contacts / responses / conversions / cost
- `MIS_FACT_ADVISOR_PERF` — monthly advisor plans, KPI scores (0–100) and 1–5 ratings
- `MIS_FACT_BRANCH_PLAN` — monthly branch plans (sum of its advisors' plans)

Data is generated deterministically (fixed RNG seeds), so every `seed.py` run
produces identical numbers.

## API

| Endpoint | Description |
|---|---|
| `GET /` | dashboard UI |
| `GET /api/meta` | available months + cache status |
| `GET /api/report/{id}?from=YYYY-MM&to=YYYY-MM&dim=branch` | report payload (cached) |
| `GET /api/drill/{id}/{branches\|advisors\|sales}?key=CODE&from=&to=` | row-level drill-down |
| `GET /api/drill/{id}/{target}?key=CODE&fmt=xlsx` | drill view as spreadsheet |
| `GET /api/export/{id}/{synthetic\|analytic}/{xlsx\|xlsb}?from=&to=&dim=` | spreadsheet download |
| `GET /api/ledger?from=&to=&q=&group=&channel=&status=&sort=&dir=&page=&page_size=` | paginated, filtered sales detail |
| `GET /api/ledger/export/{xlsx\|xlsb}?filters...` | filtered ledger as spreadsheet |
| `GET /api/people/advisors` / `GET /api/people/branches` | picker lists |
| `GET /api/people/advisors/{code}` | advisor profile + ratings panel |
| `GET /api/people/cumulative?scope=branch\|advisor&code=&month=` | cumulative vs plan vs previous month |
| `GET /api/people/advisors/{code}/export/{fmt}` / `.../cumulative/export/{fmt}` | panel / cumulative spreadsheets |
| `POST /api/cache/refresh` | force full table reload |
| `GET /api/status` | DB + pool + cache stats |

Report ids: `overview, loans, investments, insurance, current_accounts,
ror_balances, savings_accounts, term_deposits, clients, penetration, campaigns`.

Exports: reports that declare no analytic table (e.g. `overview`) answer
`/api/export/{id}/analytic/...` with **400**; the UI hides those buttons.
Sheet names are sanitized for Excel/XML (e.g. `&` → `and`), because xlspy does
not escape them in the workbook's filter definitions.