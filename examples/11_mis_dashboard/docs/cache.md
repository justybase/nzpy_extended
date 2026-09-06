# Cache and ETL publication contract

> Document type: current cache contract and operational guidance
> Status: maintained
> Production adaptation: topology, encryption and stale-data policy required
> Owner: data engineering / platform architecture

This document describes why the dashboard uses an application cache, what is
cached, how an ETL process publishes a new dataset, and how operators can
diagnose freshness. The design uses ordinary Netezza tables only. It does not
use materialized views, CTAS tables owned by the application, or a separate
distributed cache service.

For the complete module map, repository contract and extension checklist, see
[`implementation-guide.md`](implementation-guide.md).

## Decision and motivation

The dashboard serves reports, drill-downs, a large sales ledger, cumulative
views, exports and point-in-time views. Many user actions change only a filter,
sort order or page. Re-reading the same Netezza fact tables for each of those
actions would create avoidable scans, latency and workload on Netezza.

The selected pattern is:

1. Netezza remains the system of record for the ordinary `MIS_*` tables.
2. The application keeps a process-local RAM copy of the data needed by the
   dashboard and a durable SQLite snapshot for restart recovery.
3. The ETL publishes a small version marker in the ordinary
   `MIS_CONTROL_DATASET_LOAD` table only after a complete, validated batch is
   available.
4. The application polls that small table and reloads the larger tables only
   when the published version changes.

This separates two responsibilities. ETL decides when a coherent dataset is
ready; the application decides when that dataset needs to be loaded into
memory. A fixed timer is not used to re-read large tables because it cannot
know whether data changed. A short polling query is inexpensive and gives a
bounded delay between publication and detection.

### Why ordinary tables, not materialized views

The dashboard deliberately reads ordinary Netezza tables. This keeps the data
model visible in `seed.py`, makes the ETL publication boundary explicit, and
does not introduce a database-managed materialization lifecycle. The cache is
an application performance mechanism, not another warehouse data product.

The trade-off is that each application process has its own memory cache. The
SQLite snapshot removes the mandatory full reload after restart, but this
example still assumes one process/host; it is not a distributed cache.

## Cache layers

| Layer | Contents | Netezza access | Invalidation |
|---|---|---|---|
| Publication control | Latest `PUBLISHED` row in `MIS_CONTROL_DATASET_LOAD` | One small query per polling interval, plus startup/manual refresh | Never copied into the data cache; read directly |
| Eager table cache | Dimensions and regular facts listed in `Settings.table_names`, excluding the lazy snapshot mart | RAM restore from SQLite, or full table read after a new ETL version/manual refresh | Complete generation swap in RAM and SQLite |
| Lazy snapshot cache | Requested equality slices of `MIS_FACT_PERFORMANCE_SNAPSHOT`, normally by `snapshot_date` | RAM, then persisted SQLite slice, then one filtered Netezza query | Cleared with the active SQLite generation |
| Report payload cache | Computed report and drill responses | No Netezza query when the payload is cached | Two-minute TTL by default and dataset refresh |
| People/temporal caches | Advisor panels, cumulative views and temporal calculations | Computed from the table cache | Cleared after a successful dataset swap; short TTL where configured |
| Ledger join cache | Joined sales-detail rows used for filtering, sorting and paging | Built from cached sales and dimensions | Cleared after a successful dataset swap |

The eager table cache currently contains the ordinary dimensions and facts:

`MIS_DIM_REGION`, `MIS_DIM_BRANCH`, `MIS_DIM_ADVISOR`, `MIS_DIM_PRODUCT`,
`MIS_DIM_CHANNEL`, `MIS_DIM_CAMPAIGN`, `MIS_DIM_DATE`,
`MIS_DIM_ORG_ASSIGNMENT`, `MIS_FACT_SALES`, `MIS_FACT_BALANCES`,
`MIS_FACT_CUSTOMER_MOVEMENT`, `MIS_FACT_CAMPAIGN_RESULTS`,
`MIS_FACT_BRANCH_PLAN`, `MIS_FACT_ADVISOR_PERF`, `MIS_DIM_USER` and
`MIS_AUDIT_SNAPSHOT_LOAD`.

`MIS_FACT_PERFORMANCE_SNAPSHOT` is kept lazy because it is the larger
point-in-time mart. The application pushes an equality predicate to Netezza
and caches only the requested slice. The control table is not in
`Settings.table_names` and is never loaded through `CachedMISRepository`;
this prevents a stale control row from being hidden by the data cache.

## Cache-table retention versus freshness

The table cache is version-driven, not timer-driven. `CachedMISRepository`
uses bounded LRU containers only to limit memory consumption. An LRU eviction
is a memory decision; it is not a signal that the ETL published new data.

`NZ_CACHE_TTL_TABLES` remains exposed as a compatibility/status setting and is
set to `86400` seconds (24 hours). It documents the intended retention window,
but it does not cause a large-table query every 24 hours. The actual sequence
is:

- a new published ETL version causes a refresh immediately after the next
  control poll;
- a manual reload can force a refresh;
- if the control query fails, the application marks the data `stale`
  immediately and continues serving the last complete generation;
- if no successful freshness confirmation is possible for 24 hours, the
  application also marks the data `stale`;
- the application does not silently replace a complete generation with a
  partial one and does not discard usable data merely because the warning
  threshold was reached.

This behaviour is useful for a reporting dashboard: availability is preserved,
while an operator and the user interface can see that freshness is no longer
confirmed. A hard fail or hard expiry can be added as a separate policy if a
future business requirement makes stale reporting unacceptable.

## ETL publication contract

The ETL process owns the publication boundary. A valid batch should follow this
order:

1. Load or replace the ordinary dashboard tables.
2. Build and validate the reporting mart and its reconciliation evidence.
3. Write a `PASS` row to `MIS_AUDIT_SNAPSHOT_LOAD` for every reportable
   snapshot that is meant to be available.
4. Run row-count, watermark, referential and business-quality checks.
5. Insert the `PUBLISHED` row into `MIS_CONTROL_DATASET_LOAD` last.

The application treats the latest row matching both the configured
`dataset_name` and `status = 'PUBLISHED'` as the publication signal. The
version identity is the tuple:

```text
(dataset_name, version_no, load_id)
```

`version_no` must increase monotonically for a dataset and `load_id` must be
unique for every batch. `source_max_date`, `row_count` and `checksum` are
optional evidence fields, but they should be populated by a production ETL
where available. Older control rows are retained as publication history.

The control row must be written only after the ordinary tables are stable. If
the ETL publishes first and continues changing those tables, the application
can observe a version that does not yet represent a coherent dataset. The
publication-last rule is therefore a correctness requirement, not merely an
optimization.

The demo seed creates one initial row:

```text
dataset_name = MIS_DASHBOARD
version_no   = 1
load_id      = SEED-20260815
status       = PUBLISHED
```

The complete table definition and all keys are documented in
[`data_dictionary.md`](data_dictionary.md) and visualized in
[`erd.md`](erd.md). `MIS_CONTROL_DATASET_LOAD` intentionally has no business
foreign keys: it identifies a complete dataset generation rather than a
business entity.

## Application lifecycle

### Startup

`CacheCoordinator.initialize()` first tries to restore the complete eager
generation from SQLite. It then reads the latest published control row. A
matching `(dataset_name, version_no, load_id)` avoids the large Netezza read.
An older, missing or invalid snapshot triggers the normal complete refresh.
The background poller starts only after restore or refresh has completed.

If the control table is not present yet, the application still starts in
compatibility mode and serves the loaded ordinary tables. The status endpoint
exposes the control-table error. Once the table exists, the next successful
poll establishes the version contract.

### Periodic control poll

Every five minutes by default, the coordinator executes one direct query
against the small control table:

```sql
SELECT dataset_name, version_no, load_id, source_max_date,
       published_at, status, row_count, checksum
FROM MIS_CONTROL_DATASET_LOAD
WHERE dataset_name = ?
  AND status = ?
ORDER BY version_no DESC
LIMIT 1
```

If the version tuple is unchanged, the coordinator does not read any dataset
table and does not clear derived caches. This is the normal, inexpensive path.

If the tuple changed, it reads every eager table into a separate in-memory
replacement set. Only after every read succeeds does it switch the complete
set into service. The lazy snapshot slices and derived caches are then cleared,
so no calculation built from the previous generation survives the swap.

### Manual reload

`POST /api/cache/refresh` uses the same coordinator as the background poll but
forces a complete eager-table reload. It is available only to technical roles
that have `can_refresh_cache`, currently the MIS SQL developer and HQ full
access personas. A successful reload clears the lazy and derived caches. A
failed reload leaves the previous complete generation in service.

### Failed refresh

The refresh is all-or-nothing from the application point of view:

- table reads are first collected in a temporary replacement set;
- an error in any eager table prevents the replacement from being committed;
- the old table cache, metadata and derived results remain available;
- `refresh_error` and `stale` are exposed to the API and UI;
- the next control poll retries the refresh while the published version is
  still different from the version in memory.

This protects reports from mixing dimensions and facts from different ETL
generations. It also makes a transient Netezza or network failure visible
without turning it into an unnecessary outage for users.

### Control-table failure

If the control query fails, the coordinator does not reload the large tables
and does not clear any cache. It retains the last control version and the last
complete data generation, records `control_error`, and retries on the next
poll. The `unconfirmed_age_seconds` clock is based on the last successful
freshness confirmation (or the last successful initial table load when no
control confirmation has ever succeeded).

The control error marks `stale` immediately, while data remains readable and
the API and sidebar display a warning. The
`NZ_CACHE_MAX_UNCONFIRMED_SECONDS` threshold also marks the data stale when no
successful freshness confirmation has been possible for that long. A
successful control query clears the warning if the published version is
already loaded; a changed version additionally triggers the normal refresh.

### Process restart and multiple workers

Restarting the supported single process restores the last complete generation
from `NZ_CACHE_SQLITE_PATH`. If Netezza is unavailable, that generation remains
readable and the status is marked stale when freshness cannot be confirmed.
The SQLite file contains the full unmasked cache. The example only assumes
that the configured directory is writable by the application and is handled
according to the target environment's normal access policy; exclusive access
for the application account is not a requirement of this example.

Multi-worker and multi-host coordination are outside this example's scope. A
future deployment of that kind must add cross-process locking and a shared
storage policy; the current in-process refresh lock is not sufficient.

## Quality gates and point-in-time reporting

`MIS_CONTROL_DATASET_LOAD` answers: “which complete ETL dataset generation is
published?” It does not replace `MIS_AUDIT_SNAPSHOT_LOAD`, which answers:
“is this exact reporting cut-off reconciled and eligible?”

For an `as_of` request, the report and temporal services require an audit row
with `status = 'PASS'` and `source_max_date = as_of`. A fresh application cache
cannot make a failed or unavailable snapshot reportable. Conversely, a valid
audit row does not make an unconfirmed application cache fresh; both signals
are kept visible in the reporting context and cache status.

This preserves the existing DTD/MTD/PMTD/MoM/YTD/YoY semantics, historical or
current attribution, SCD2 organization history, and Champions League
eligibility guardrails while avoiding repeated reads of the underlying tables.

## Observability and diagnostics

The following authenticated endpoints expose cache state:

- `GET /api/status` — database, pool and cache statistics;
- `GET /api/meta` — report metadata, available months/snapshots and cache
  status;
- report responses — `freshness` describing the generation used to calculate
  the payload.

Important status fields are:

| Field | Meaning |
|---|---|
| `cache_mode` | `etl_versioned_sqlite` when durable persistence is enabled |
| `dataset_name` | Logical ETL dataset being monitored |
| `dataset_version`, `load_id` | Latest published control identity |
| `refreshed_version` | Version of the complete generation currently in memory |
| `control_checked_at` | Last control-table query, successful or failed |
| `control_check_ok` | Whether the most recent control query succeeded |
| `control_error` | Last control query error, if any |
| `last_refresh_at` | Last successful eager-table commit |
| `last_refresh_reason` | `startup`, `manual` or published-version reason |
| `refresh_error` | Last incomplete refresh error, if any |
| `cache_age_seconds` | Age of the last successful table commit |
| `unconfirmed_age_seconds` | Age since the last successful freshness confirmation |
| `max_unconfirmed_seconds` | Warning threshold, 86400 by default |
| `stale` | Version mismatch, refresh failure or expired confirmation warning |
| `hits`, `misses` | Process-local table/slice cache counters |
| `tables` | Row count and load timestamp for each cached table |
| `report_cache_size`, `report_cache_ttl` | Derived report cache state |
| `persistent_snapshot_present` | Whether the active SQLite snapshot exists |
| `persistent_snapshot_version`, `persistent_snapshot_load_id` | Generation represented on disk |
| `restored_from_disk` | Whether the current process started from SQLite data |
| `persistent_error` | Last SQLite restore, staging or lazy-slice error |
| `lazy_persistent_hits`, `lazy_persistent_misses` | Disk-backed lazy-slice counters |

The UI's **Data cache** panel presents the same information, including the
loaded generation, control-check time, age and warnings. A report freshness
line also includes the generation snapshot identifier, so a response can be
traced back to the table generation from which it was built.

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `NZ_CACHE_TTL_TABLES` | `86400` | Compatibility/status retention window; table refresh is version-driven |
| `NZ_CACHE_TTL_REPORTS` | `120` | TTL for computed report payloads |
| `NZ_CACHE_CONTROL_POLL_SECONDS` | `300` | Polling interval for the small control table |
| `NZ_CACHE_MAX_UNCONFIRMED_SECONDS` | `86400` | Freshness-warning threshold |
| `NZ_CACHE_DATASET_NAME` | `MIS_DASHBOARD` | Logical dataset name selected from the control table |
| `NZ_CACHE_CONTROL_TABLE` | `MIS_CONTROL_DATASET_LOAD` | Ordinary control-table name |
| `NZ_CACHE_SQLITE_PATH` | `var/cache/mis_dashboard.sqlite3` | Durable local snapshot; empty disables persistence |

The control-table identifier is validated before being interpolated into the
SQL statement. Dataset name and status are query parameters. In production,
the configured control-table name should be a fixed trusted identifier and the
database account should have only the required read access.

## Operational runbook

### Initial demo setup

`seed.py` creates and loads the ordinary schema, including the control table.
It drops and recreates the demo MIS tables, so use it only against a
disposable/demo database:

```bash
python seed.py
python -m uvicorn main:app --host 127.0.0.1 --port 8481
```

The current application has a compatibility fallback so an older database
without `MIS_CONTROL_DATASET_LOAD` can still serve its ordinary cached data.
That fallback is useful during rollout, but it does not provide ETL version
detection. Run the updated seed (or an equivalent controlled schema migration)
before relying on automatic version-driven refreshes.

### When a new ETL batch is published

After all ordinary tables and audit rows are ready, insert one new control row
with a higher version. Do not update the control row before the data batch is
complete. The application will detect it on the next poll, normally within
the configured polling interval, and will report the new `refreshed_version`
after a successful complete read.

### When the status is stale

1. Check `control_check_ok` and `control_error`.
2. Check whether `dataset_version` differs from `refreshed_version`.
3. Check `refresh_error` and the Netezza connection/pool state.
4. Verify that the ETL inserted the control row last and marked it
   `PUBLISHED`.
5. After correcting the cause, wait for the next poll or use **Reload data**
   with a permitted technical persona.

Do not manually clear only one derived cache. The coordinator owns the cache
generation boundary and clears all dependent caches together.

## Tests covering the contract

`tests/test_cache_coordinator.py` covers unchanged versions, version changes,
derived-cache invalidation, failed refresh retry, the 24-hour warning and
control-table identifier validation. The rest of the dashboard test suite
covers the report engine, snapshot quality gates, role scopes, seeded keys and
API behaviour. The repository's full example test suite should be run after
changes to the cache or seed contract.
