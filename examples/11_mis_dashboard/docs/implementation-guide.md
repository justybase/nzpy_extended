# MIS Dashboard — implementation and extension guide

> Document type: implementation reference
> Status: maintained
> Applies to: current example and extension work
> Production adaptation: required
> Owner: application architecture

This is the canonical implementation map for the example application. It
explains where behaviour belongs, which contracts are stable, and what must be
updated when the reporting model changes.

The example is intentionally close to a production FastAPI reporting service,
but it is not a production deployment. Netezza remains the system of record;
the application reads, caches, authorizes and presents reporting data. ETL,
identity-provider integration, secret management, deployment orchestration and
cross-host coordination remain external responsibilities.

## Source-of-truth order

When a description and an implementation appear to disagree, resolve the
question in this order:

1. Executable tests define currently supported behaviour.
2. `seed.py` and `sql/reporting_mart.sql` define the demo schema and generated
   data.
3. `app/schemas/` and route declarations define the HTTP response contract.
4. `docs/data_dictionary.md` and `docs/erd.md` define table grain and
   relationships.
5. `docs/cache.md` defines the ETL publication and cache lifecycle.
6. `docs/contracts/` defines generated HTTP/data contracts and API conventions.
7. The README describes product scope, setup and visible features.

Do not derive authorization, data grain or freshness semantics from the
frontend alone. The backend is authoritative for all three.

## Contents

- [Reading order](#reading-order)
- [Runtime architecture](#runtime-architecture)
- [Repository contract](#repository-contract)
- [Dataset and cache invariants](#dataset-and-cache-invariants)
- [Request lifecycle](#request-lifecycle)
- [Extension recipes](#extension-recipes)
- [Database and SQL rules](#database-and-sql-rules)
- [Testing and verification](#testing-and-verification)
- [Operational checklist](#operational-checklist)

## Reading order

| Question | Start here | Then inspect |
|---|---|---|
| How do I run the example? | `README.md` | `seed.py`, `requirements.txt` |
| Which tables and grains exist? | `docs/data_dictionary.md` | `seed.py`, `docs/erd.md` |
| How is freshness detected? | `docs/cache.md` | `app/services/cache_coordinator.py`, `app/repositories/cached.py` |
| How does a report work? | `app/services/reporting.py` | `app/services/report_service.py`, `app/api/routes/reports.py` |
| How does point-in-time reporting work? | README temporal section | `app/services/temporal_service.py`, `app/repositories/temporal.py` |
| How is access restricted? | `docs/authentication.md` | `app/core/roles.py`, `app/repositories/scoped.py` |
| What does the API return? | `app/schemas/` | `app/api/routes/` |
| Which production choices remain open? | `docs/production-adaptation.md` | relevant ADR |
| How do I recover an incident? | `docs/operations.md` | `app/services/cache_coordinator.py` |
| Which behaviour is protected by tests? | `tests/` | matching service and repository modules |

## Runtime architecture

The normal dependency direction is:

```text
HTTP request
    -> FastAPI route
    -> dependency from app.state
    -> service
    -> MISRepository interface
    -> CachedMISRepository
    -> RAM cache / SQLite snapshot / Netezza pool
```

The application is assembled in `main.create_app()` during the FastAPI
lifespan. The lifespan creates the pool, repository, services and
`CacheCoordinator`, then initializes the cache before starting the background
control-table poller.

### Module responsibilities

| Module | Responsibility | Must not own |
|---|---|---|
| `main.py` | Composition root, lifespan, application state | Report definitions or row-level authorization |
| `app/api/routes/` | HTTP parameters, dependency injection, status-code mapping | SQL, business aggregation, frontend-only security |
| `app/api/deps.py` | Resolve services and authenticated user from `Request` | Mutable global user state |
| `app/services/reporting.py` | Pure report definitions, aggregation and chart/table payloads | HTTP concerns, direct Netezza connections |
| `app/services/report_service.py` | Report orchestration, user-aware cache keys, freshness metadata | Table-specific SQL or role policy definitions |
| `app/services/temporal_service.py` | Snapshot quality gates, DTD/MTD/PMTD/MoM/YTD/YoY and point-in-time logic | Authentication and cookie handling |
| `app/services/ledger_service.py` | Large sales-detail join, filtering, sorting, paging and export rows | Unbounded HTTP response construction |
| `app/services/people_service.py` | Advisor/branch views and cumulative personal results | Global process user state |
| `app/services/cache_coordinator.py` | ETL-version polling, generation refresh and dependent-cache invalidation | Report calculations |
| `app/repositories/base.py` | Storage interface and fallback slice implementation | Concrete database credentials |
| `app/repositories/cached.py` | RAM cache, lazy slices and persistent snapshot integration | HTTP status codes |
| `app/repositories/scoped.py` | Backend row-level masking for a `SessionUser` | UI-only filtering |
| `app/repositories/temporal.py` | As-of table shaping and current/historical attribution wrapper | Authentication decisions |
| `app/repositories/sqlite_snapshot.py` | Local snapshot staging, validation and atomic activation | Netezza reads or report semantics |
| `app/schemas/` | Pydantic response contracts | Business data loading |
| `app/core/roles.py` | Role sets, scope properties and capability flags | Query execution |
| `app/db/pool.py` | Netezza pool creation and connection lifecycle | Cache invalidation |

### Objects stored in `app.state`

The following names are part of the composition contract used by dependency
functions and tests:

```text
settings, pool, repository, report_service, export_service,
ledger_service, people_service, session_service, auth_service,
temporal_service, cache_coordinator
```

If a new service is added, wire it in `main.py`, expose it through
`app/api/deps.py`, and add a focused service test. Do not instantiate services
inside individual routes.

## Repository contract

Every repository implementation must satisfy `MISRepository`:

```python
async def get_table(name: str) -> tuple[list[str], list[list[Any]]]
async def get_table_slice(name: str, column: str, value: Any) \
    -> tuple[list[str], list[list[Any]]]
async def refresh_all(generation: DatasetVersion | None = None) \
    -> dict[str, Any]
async def restore_persisted() -> dict[str, Any]
def snapshot() -> dict[str, Any]
def is_ready() -> bool
@property
def last_error() -> str | None
```

Repository results use these normalization rules:

- column names are lowercase;
- rows are plain Python lists;
- dates and datetimes are ISO strings;
- `Decimal` values are converted to `float` by the cached repository;
- a slice is an equality filter and uses the first ten characters of a date
  value (`YYYY-MM-DD`);
- callers must not mutate shared cached rows in place; copy rows before
  applying a report-specific transformation.

`refresh_all()` is a generation boundary. It must not expose a partial mix of
old and new eager tables. On failure it returns an `errors` list and keeps the
previous complete generation available.

`snapshot()` is an operational payload, not a business-data API. It may contain
cache counters, timestamps, generation identity and error details, but it must
not expose raw fact rows.

## Dataset and cache invariants

### Dataset identity

The ETL publication identity is the immutable tuple:

```text
(dataset_name, version_no, load_id)
```

It is represented by `app.core.cache_types.DatasetVersion`. `generation_id`
is a stable string derived from the same values and is used to associate lazy
SQLite slices with one eager generation.

`MIS_CONTROL_DATASET_LOAD` is queried directly and is not placed in
`Settings.table_names`. The application reads the latest row with the
configured `dataset_name` and `status = 'PUBLISHED'`. The ETL must publish that
row only after its complete load and quality checks are finished.

This example deliberately does not add a per-table manifest. ETL owns row
count, watermark, checksum, referential and business-quality checks; the
application trusts the published generation contract and verifies the local
SQLite snapshot structure before restoring it.

### Cache layers

| Layer | Contents | Read order | Invalidation |
|---|---|---|---|
| RAM eager cache | Dimensions and regular facts | RAM, then Netezza on a cold load | Complete generation swap |
| SQLite eager snapshot | Last complete eager generation | Startup restore only; reports still read RAM | Atomic replacement on generation commit |
| RAM lazy cache | Requested performance-snapshot slices | RAM, then SQLite, then filtered Netezza query | Cleared on generation swap |
| SQLite lazy slices | Requested slices tied to `generation_id` | Before a cold lazy query | Replaced with the active SQLite generation |
| Derived service caches | Reports, people, temporal and ledger joins | Service-specific cache | Cleared by `CacheCoordinator` after a successful generation swap |

RAM is the normal report-serving layer. SQLite is a restart/recovery layer and
is not a substitute for the reporting repository interface or the Netezza
system of record.

### Startup and refresh state machine

```text
START
  |
  +-- valid SQLite snapshot? -- no --> read eager tables from Netezza
  |                                      |
  |                                      +--> stage SQLite generation
  |                                      +--> swap RAM generation
  |
  +-- yes --> restore eager tables to RAM
             |
             +--> read PUBLISHED control row
                    |
                    +-- same token --> serve restored generation
                    +-- new/missing --> full eager refresh
                    +-- control error --> serve restored generation, mark stale

POLL
  |
  +-- unchanged token --> no large-table query
  +-- changed token --> all eager reads -> stage -> atomic swap -> clear dependants
  +-- query error --> keep last complete generation, mark stale
```

The refresh order is important: load all eager tables into a replacement set,
stage the SQLite file, activate the persistent file, swap the RAM cache and
clear derived caches. Never clear derived caches before the new complete
generation is ready.

### Freshness and stale data

`stale = true` means that freshness cannot currently be trusted; it does not
mean that the last complete data is deleted. The application continues serving
the last complete generation when:

- the control table is unavailable;
- a refresh fails for one or more eager tables;
- the SQLite snapshot cannot be written but the RAM refresh succeeds; or
- the freshness-confirmation threshold has expired.

The API must expose the reason through `control_error`, `refresh_error` or
`persistent_error`. A successful control query clears a control error. A new
published version is not considered active until the complete refresh commits.

## Request lifecycle

### Authenticated request

1. Public routes are limited to the root page, static assets and login/logout.
2. Data routes resolve `get_current_user()` from the `HttpOnly` cookie.
3. The route validates HTTP-specific values such as `fmt`, `dim`, `page_size`
   and `attribution`.
4. The service performs business validation and reads through the repository.
5. Scoped users are filtered in backend services/repositories, not in the
   browser.
6. The response is validated by a Pydantic model where one is declared.

Missing, expired or invalid credentials return `401`. A valid user outside a
permitted scope receives `403` or an empty scoped result according to the
service contract. Do not rely on hiding a selector or table in JavaScript as
an authorization control.

### Report request

`GET /api/report/{report_id}` follows this path:

```text
route validation
  -> ReportService.build()
  -> snapshot_id + user scope cache key
  -> optional as_of audit validation
  -> reporting.build()
  -> MISRepository reads
  -> ReportResponse payload
```

The report payload convention is:

```json
{
  "id": "loans",
  "title": "...",
  "subtitle": "...",
  "period": {"from": "2026-01", "to": "2026-08"},
  "kpis": [],
  "insights": [],
  "freshness": {},
  "synthetic": {"columns": [], "rows": []},
  "dims": [],
  "analytic": {"columns": [], "rows": []},
  "charts": []
}
```

`freshness` must identify the data generation used for the response. Report
cache keys must include the effective user scope and all filters that alter
the result. Never cache a full-scope payload under a scoped user's key.

### Point-in-time request

For `as_of` reporting:

1. Parse and validate `YYYY-MM-DD`.
2. Find the matching `MIS_AUDIT_SNAPSHOT_LOAD` row.
3. Require `status = 'PASS'` and `source_max_date = as_of`, unless an
   explicitly administrative `allow_failed` path is intended.
4. Use historical or current attribution explicitly.
5. Push `snapshot_date` equality to `MIS_FACT_PERFORMANCE_SNAPSHOT` through
   `get_table_slice()`.
6. Include `as_of`, `data_through`, `load_id`, `loaded_at` and `attribution`
   in the reporting context.

Do not infer point-in-time eligibility from the existence of a row alone. The
audit quality gate and the data snapshot are separate contracts.

## Extension recipes

### Add a report

1. Define the business meaning first: grain, date basis, status filters,
   measures, denominator, comparison period, authorization behaviour and
   empty-data behaviour.
2. Add the calculation and any helper/chart builder to
   `app/services/reporting.py`.
3. Register the report in `REGISTRY` with its title, dimensions, synthetic
   builder, analytic builder and charts.
4. Add drill targets only when the key-to-child relationship is explicit and
   covered by the data dictionary.
5. Keep calculations over repository data; do not open a database connection
   from a report definition.
6. Add report-engine tests for totals, empty periods, comparison periods and
   dimension output.
7. Add service/API tests when authorization, `as_of`, caching or HTTP errors
   are involved.
8. Update the README report list and any UI navigation only after the backend
   contract is complete.

### Add a table or change a table

Update all applicable sources together:

1. `seed.py` and/or `sql/reporting_mart.sql` for the demo schema/load;
2. `Settings.table_names` in `app/core/config.py` for an eager table;
3. the lazy-table path only if equality-slice semantics are intentional;
4. `docs/data_dictionary.md` with grain, keys, relationships and column
   meanings;
5. `docs/erd.md` when relationships change;
6. `docs/cache.md` when the load or invalidation contract changes;
7. repository and report tests.

Never add `MIS_CONTROL_DATASET_LOAD` to the ordinary table cache. It is the
freshness signal and must be read directly. For a large fact table, decide
explicitly whether full eager loading is safe; otherwise define a bounded
slice key and document its persistence/invalidation behaviour.

### Add an endpoint

1. Put request/response models in `app/schemas/` when the payload is stable.
2. Add a thin route under `app/api/routes/`.
3. Resolve dependencies through `app/api/deps.py`.
4. Put business rules in a service, not in the route.
5. Add explicit authentication and capability checks.
6. Map domain exceptions to deliberate HTTP status codes.
7. Add an API test for success, unauthenticated access, invalid input and
   out-of-scope access where relevant.
8. Add the route to the README endpoint table.

### Add or change a role

1. Define the canonical role and scope in `app/core/roles.py`.
2. Update seeded `MIS_DIM_USER` rows and `docs/authentication.md`.
3. Apply authorization in the service/repository path.
4. Include the role in capability checks such as cache refresh or global
   quality visibility.
5. Test both allowed and forbidden entities. A frontend-only role change is
   incomplete.

### Change cache behaviour

1. State whether the change affects RAM, SQLite, lazy slices, report payloads
   or derived service caches.
2. Preserve generation atomicity and the old complete generation on failure.
3. Pass `DatasetVersion` through wrappers and fake repositories.
4. Update `snapshot()` and `CacheInfo` for operator-visible state.
5. Test startup restore, same-version no-op, version change, control failure,
   failed refresh and lazy-slice persistence as applicable.
6. Update `docs/cache.md` and the operational runbook.

## Database and SQL rules

- Use parameter binding for values.
- If an identifier must be interpolated, validate it against a strict
  identifier pattern first; the control-table name is the existing example.
- Keep source reads behind the repository or a dedicated data-access service.
- Prefer one cached table read plus in-memory aggregation over repeated fact
  scans for every filter interaction.
- Preserve explicit status predicates such as `BOOKED` where the metric
  definition requires them.
- Document every join's expected cardinality. A many-to-many join can inflate
  measures without raising an exception.
- Treat Netezza as read-only from this application. ETL owns publication and
  data quality.

## Testing and verification

Run from `examples/11_mis_dashboard` or use paths from the repository root:

```bash
python -m pytest tests/ -q
python -m compileall -q app tests
python tools/verify_documentation.py
python tools/export_openapi.py --check
```

Recommended focused tests after each type of change:

| Change | Tests |
|---|---|
| Report calculation | `test_report_engine.py`, `test_report_service.py` |
| Cache/version lifecycle | `test_cache_coordinator.py`, `test_sqlite_snapshot.py` |
| API/authentication | `test_auth_api.py`, relevant route tests |
| Roles and scope | `test_auth.py`, `test_session.py`, `test_ledger_people.py` |
| Temporal/as-of rules | `test_temporal_mis.py`, `test_report_service.py` |
| Seed/schema | `test_seed_schema.py` |
| Documentation/contracts | `test_documentation_contracts.py` |

Live database tests must use a disposable database and explicit
`NZ_DEV_*` credentials. Unit tests must not depend on source-code credentials,
an existing SQLite file, or a running server. Do not commit `var/`, temporary
SQLite files, generated exports, screenshots from ad-hoc runs or secrets.

## Operational checklist

Before treating an implementation as complete, verify:

- [ ] the business grain and time semantics are documented;
- [ ] the published dataset identity is unambiguous;
- [ ] a failed load cannot expose a partial generation;
- [ ] report cache keys include user scope and all result-changing filters;
- [ ] `as_of` paths enforce the audit quality gate;
- [ ] row-level authorization is enforced in backend code;
- [ ] all dynamic SQL identifiers are validated;
- [ ] status and error information is observable;
- [ ] OpenAPI and data contracts are generated/validated;
- [ ] production adaptation rows have owners and evidence criteria;
- [ ] the SQLite directory access and retention policy match the target environment;
- [ ] multi-worker deployment assumptions are documented before scaling out;
- [ ] focused tests and the complete example suite pass.
