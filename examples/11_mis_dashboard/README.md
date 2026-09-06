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
│       ├── temporal.py        point-in-time, hierarchy, league, quality APIs
│       ├── drill.py           GET /api/drill/{id}/{target} (row drill-down)
│       ├── exports.py         GET /api/export/... (xlsx/xlsb)
│       ├── cache.py           POST /api/cache/refresh
│       └── status.py          GET /api/status
├── services/                  business logic
│   ├── reporting.py           report definitions + pure-Python aggregations
│   ├── report_service.py      orchestration + report payload TTLCache
│   ├── ledger_service.py      sales ledger: server-side pagination/filtering
│   ├── people_service.py      advisor panels + cumulative personal views
│   ├── temporal_service.py    DTD/MTD/PMTD/MoM/YTD/YoY + temporal hierarchy
│   ├── session_service.py     simulated sign-in + role-based scope rules
│   └── export_service.py      xlspy workbook generation
├── repositories/              data access
│   ├── base.py                MISRepository interface (ABC)
│   ├── cached.py              eager tables + lazy Netezza slices in TTLCache
│   ├── temporal.py            as-of cut-off + current/historical attribution
│   └── scoped.py              row-level role masking (ScopedMISRepository)
├── schemas/                   Pydantic response models
├── core/                      settings, logging, role model (roles.py)
└── db/                        Netezza connection pool factory
```

Plus `tools/visual_check.py` — browser-based layout checks (Playwright).

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
| Daily MIS | **Point-in-time performance** with DTD, MTD, PMTD, MoM, YTD and YoY |
| Organization | SCD2 hierarchy browser and historical/current attribution switch |
| Motivation | Guardrailed **Champions League** for advisors and branches |
| Governance | Data freshness, reconciliation, continuity, uniqueness and integrity checks |
| Personal | **My branch** (branch manager) and **My results** (advisor) — cumulative sales within the month vs plan and vs previous month |
| Personal | **Daily brief** — your results + top network KPIs on one print-ready A4 page |
| Access | **Role-based scope** — simulated sign-in (analyst / area manager / branch manager / advisor) enforced on the backend |
| Detail | **Sales ledger** — the full sales list with server-side pagination, free-text search, filters and sorting |
| Overview | KPI cards, plan attainment, decision insights and charts across the whole network |

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

Report KPIs also expose a comparable previous period, optional target and
status (`good`, `warning`, `critical`), while the Overview adds a short,
deterministic action-oriented insight list. The period toolbar supports
presets (last 3 months, last 12 months and year to date) and report context is
kept in the URL.

## Point-in-time reporting: what `as_of` means

The canonical open-period example is **2026-08-15**. It contains activity from
1–15 August and never silently includes 16–31 August. The exact snapshot,
source watermark, load id, reconciliation status and hierarchy attribution are
visible in the UI and returned by the API.

| Code | Definition |
|---|---|
| DTD | selected day's activity versus the immediately preceding snapshot day |
| MTD | 1st calendar day of the month through `as_of` |
| PMTD | the matching day range in the preceding month |
| MoM | last fully closed month versus the preceding fully closed month |
| YTD | 1 January through `as_of` versus the matching prior-year range |
| YoY | current MTD versus the matching month/day one year earlier |

Monthly plans are independent of the result they measure: demo targets use
preceding-month history, seasonality and deterministic noise. Plan pacing and
the month-end forecast use business days; MTD itself remains a calendar range.

### Temporal organization

`MIS_DIM_ORG_ASSIGNMENT` is a type-2 slowly changing dimension at advisor
assignment grain. The seed includes several transfers, including `P0037` on
2026-08-08. The structure switch demonstrates two legitimate reporting views:

- **Historical** keeps an event in the branch/region valid on its activity date.
- **Current at snapshot** restates the selected history to the assignment valid
  on `as_of`.

Attribution happens before row-level authorization. A manager therefore sees
only the reporting units in their permitted scope after the chosen attribution.
Network totals remain equal between the two modes; organizational subtotals may
differ.

### Champions League with guardrails

The league score is deliberately explainable: plan attainment 50%, PMTD
momentum 20%, quality 15% and activity 15%. Attainment is capped at 120% for
scoring. Qualification requires at least 10 booked sales and quality of at
least 80/100. The UI exposes the formula, qualification and badges rather than
turning a single large transaction into an opaque contest. Advisors see their
scoped rows and position; managers and analysts can inspect their full
authorized table. No public "worst performer" board is produced.

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
attendance %, rating, note) — downloadable as XLSX/XLSB. The selected
`as_of` snapshot cuts current-month achieved sales and hides future rating
months. Data comes from the new `MIS_FACT_ADVISOR_PERF` table.

### Daily brief — one printable A4 page

`Daily brief` (menu → My views) combines **My results / My branch** (scope
toggle) with the top **network KPIs** (sales in period, loan volume, clients
acquired, campaign ROI) into a single page: header, KPI cards (MTD, plan,
attainment, vs previous month, daily average, run-rate to plan), the
cumulative chart and the day-by-day table, with a generated/footer strip.
The **Print / PDF** button uses `@media print` CSS (A4 portrait, sidebar and
controls hidden) — exactly what prints is what you see.

### Role-based access (simulated sign-in)

No real authentication in this example — the sidebar's **"Signed in as"**
selector simulates a login from the `MIS_DIM_USER` table (297 users):

| Role | Can see |
|---|---|
| Analyst (`NET01`) | the whole network (default) |
| Area manager (`AM_*`, one per region) | branches and advisors of their region |
| Branch manager (`BM_*`, one per branch) | their branch and its advisors |
| Advisor (`ADV_*`, one per active advisor) | themselves and their own branch |

The scope is **enforced in the service layer**, not just hidden in the UI:
the picker endpoints return only the allowed entities, and `advisor_panel` /
`cumulative` (and their exports) answer **403** for anything outside the
user's scope. Pickers lock (single option) when a role has only one choice.

**The MIS report pages are masked too.** A `ScopedMISRepository` wraps the
cached tables and filters the fact rows (sales, balances, client movement,
campaign results, plans, advisor performance) by the signed-in user's scope —
so overview, all sales reports, the sales ledger, drill-downs and every
XLSX/XLSB export show only that user's data (report payloads are cached per
user). A branch manager's Loans report contains just their branch; an
advisor's ledger only their own sales; drilling into a foreign branch returns
no rows. The report subtitle notes "data scoped to your …" when masking is
active.

### My branch / My results — cumulative within the month

Two role-oriented views (menu → My views) for a branch manager and an
individual advisor. Pick your branch/advisor and a month: day-by-day
**cumulative** sales within the month, a prorated cumulative **plan** line
(business-day pacing when `as_of` is supplied) and the **previous month's** cumulative at the
same point — as KPIs (MTD, plan, attainment %, vs previous month), a three-
series line chart and a day table. Plans come from `MIS_FACT_BRANCH_PLAN`
(branch plan = sum of its advisors' plans) and `MIS_FACT_ADVISOR_PERF`.

## Caching — why Netezza is barely queried

The user-facing requirement: *do not hit Netezza on every click*. This app
implements a two-level cache:

1. **Table/query cache** (`cachetools.TTLCache`, TTL 15 min by default) — small
   dimensions and existing facts are loaded at startup. The larger
   `MIS_FACT_PERFORMANCE_SNAPSHOT` is intentionally lazy: an equality predicate
   on `snapshot_date` is pushed to Netezza and only that slice is cached.
2. **Report cache** (`TTLCache`, TTL 2 min) — computed payloads
   (KPIs + tables + charts) are cached per (report, period, dimension, role,
   `as_of` and attribution), so repeated clicks are instant without mixing
   users or reporting views.

Netezza is contacted only when an eager table or requested snapshot slice is
cold/expired, or when you click **Reload data** in the sidebar. If a background
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

Browser-based visual checks (Playwright + headless Chromium) — screenshots
land in `tools/screenshots/`, any layout regression fails the run:

```bash
pip install playwright && python -m playwright install chromium
python tools/visual_check.py           # needs the server running
```

It checks the overview, point-in-time performance, SCD2 hierarchy, Champions
League, data-quality gate, daily brief (including print emulation), responsive
layouts and the role switcher (picker locking, badge, scoped report subtitle).

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
- `MIS_DIM_USER` — 297 simulated users (analyst, area/branch managers, advisors) for role-based access
- `MIS_DIM_DATE` — reporting calendar with calendar/business-day attributes
- `MIS_DIM_ORG_ASSIGNMENT` — temporal advisor → branch → region history (SCD2)
- `MIS_FACT_PERFORMANCE_SNAPSHOT` — daily activity and accumulating MTD mart
- `MIS_AUDIT_SNAPSHOT_LOAD` — source watermark, row count and reconciliation gate

The production-style Netezza CTAS/window-function pattern is documented in
`sql/reporting_mart.sql`. `seed.py` loads a Python reference implementation of
the same mart so database-free tests and the Netezza demo share one deterministic
expected result. This also makes it possible to compare SQL output against an
independent oracle in integration tests.

Data is generated deterministically (fixed RNG seeds), so every `seed.py` run
produces identical numbers.

## API

| Endpoint | Description |
|---|---|
| `GET /` | dashboard UI |
| `GET /api/meta` | available months, exact snapshot dates + cache status |
| `GET /api/report/{id}?from=YYYY-MM&to=YYYY-MM&dim=branch&as_of=YYYY-MM-DD&attribution=historical` | existing report with exact cut-off and attribution |
| `GET /api/performance?as_of=&attribution=&dim=region\|branch\|advisor` | daily MIS cockpit and all standard comparisons |
| `GET /api/hierarchy?as_of=&attribution=` | hierarchy tree and SCD2 changes |
| `GET /api/league?as_of=&attribution=&level=advisor\|branch` | role-scoped league and its scoring rules |
| `GET /api/quality?as_of=` | reporting gate and individual quality checks |
| `GET /api/drill/{id}/{branches\|advisors\|sales}?key=CODE&from=&to=&as_of=&attribution=` | row-level drill-down |
| `GET /api/drill/{id}/{target}?key=CODE&fmt=xlsx&as_of=&attribution=` | drill view as spreadsheet |
| `GET /api/export/{id}/{synthetic\|analytic}/{xlsx\|xlsb}?from=&to=&dim=&as_of=&attribution=` | spreadsheet download |
| `GET /api/ledger?from=&to=&as_of=&attribution=&q=&group=&channel=&status=&sort=&dir=&page=&page_size=` | paginated, filtered and temporally attributed sales detail |
| `GET /api/ledger/export/{xlsx\|xlsb}?filters...` | filtered ledger as spreadsheet |
| `GET /api/people/advisors` / `GET /api/people/branches` | picker lists |
| `GET /api/people/advisors/{code}?as_of=` | advisor profile + ratings panel, cut at an exact snapshot (role-scoped, 403 outside scope) |
| `GET /api/people/cumulative?scope=branch\|advisor&code=&month=&as_of=` | cumulative vs plan vs matching previous-month day (role-scoped) |
| `GET /api/people/advisors/{code}/export/{fmt}` / `.../cumulative/export/{fmt}` | panel / cumulative spreadsheets (role-scoped) |
| `GET /api/session/me` / `POST /api/session/user` / `GET /api/session/users` | simulated sign-in: current user, switch, directory |
| `POST /api/cache/refresh` | force full table reload and clear derived caches |
| `GET /api/status` | DB + pool + cache stats |

Report ids: `overview, loans, investments, insurance, current_accounts,
ror_balances, savings_accounts, term_deposits, clients, penetration, campaigns`.

Exports: reports that declare no analytic table (e.g. `overview`) answer
`/api/export/{id}/analytic/...` with **400**; the UI hides those buttons.
Sheet names are sanitized for Excel/XML (e.g. `&` → `and`), because xlspy does
not escape them in the workbook's filter definitions.

## Scale test — ledger paging at ~1M rows

The pagination design was validated against a **scale-500 seed** (~966k sales
rows, ~984k rows cached in memory):

| Operation | Time at 192k rows | Time at ~1M rows |
|---|---|---|
| First page (one-time join) | ~0.4 s | 1.5 s |
| Any paged request (cached join) | ~45 ms | 0.16–0.27 s |
| Deep page (page 9,999 of 19,330) | — | 0.21 s |
| Filtered (group + channel + status) | — | 0.16 s |
| Free-text search (`q=`) | — | 1.15 s (full scan) |
| Sort by amount | — | 0.27 s |
| Cumulative view (1M-row scan) | — | 0.19 s |
| XLSX export (185k rows) | — | 1.06 s, 3.3 MB |

Page loads stay in the ~0.2 s range at 1M rows because the backend joins the
cached tables once and only transfers the requested 50-row slice — Netezza is
never queried per request. Run it yourself: `python seed.py --rows 500`, start
the app, then `python seed.py` to restore the demo dataset.
