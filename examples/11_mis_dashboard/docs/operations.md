# Operations and recovery runbook

> Document type: operational runbook
> Status: maintained demo runbook
> Applies to: local example and as a template for production operations
> Production adaptation: RTO, RPO, owners and escalation paths are required

## Operating assumptions

The example is a single-process, single-host service. Netezza is the system of
record. RAM is the normal report-serving cache; SQLite is a local restart
snapshot. The application can serve the last complete generation when source
freshness cannot be confirmed, but the status is `stale`.

Production must decide whether stale reports are acceptable. Do not infer that
policy from this example.

## Status fields to inspect

Use authenticated `GET /api/status` and inspect:

| Field | Interpretation |
|---|---|
| `cache.stale` | Freshness cannot currently be trusted |
| `cache.dataset_version` | Latest published control-table version |
| `cache.refreshed_version` | Complete generation in RAM |
| `cache.control_error` | Last control-table error |
| `cache.refresh_error` | Last incomplete eager refresh |
| `cache.persistent_error` | SQLite restore/staging/lazy-slice error |
| `cache.restored_from_disk` | Current process restored data from SQLite |
| `cache.persistent_generation_id` | Generation associated with persisted data |
| `cache.unconfirmed_age_seconds` | Time since successful freshness confirmation |
| `cache.tables` | Row counts and load timestamps |
| `pool` | Netezza pool health and usage |

## Startup

1. Verify the configured database, identity and cache path.
2. Confirm the process account can read the application directory and write the
   cache directory.
3. Start the application without development reload in a controlled runtime.
4. Confirm startup logs show either a successful SQLite restore or a complete
   eager load.
5. Check `/api/status` and `/api/meta` after authentication.
6. Confirm `refreshed_version` and table row counts are plausible.
7. Confirm the first report contains a `freshness` section.

If the control table is unavailable but a valid snapshot exists, the service
may start with the last complete generation and must show `stale`.

## New ETL generation

The expected sequence is:

```text
ETL loads and validates source tables
    -> audit/reconciliation passes
    -> PUBLISHED control row is inserted last
    -> application polling detects a new token
    -> all eager tables are read into a replacement set
    -> SQLite generation is staged and activated
    -> RAM generation is swapped
    -> derived caches are cleared
```

Never manually change the control row before its source tables are coherent.
If the application reports the new `dataset_version` but keeps an older
`refreshed_version`, investigate `refresh_error` before forcing another reload.

## Control-table failure

Symptoms:

- `control_check_ok` is false;
- `control_error` is populated;
- `stale` is true;
- the last complete generation remains readable.

Safe actions:

1. Check Netezza connectivity and pool statistics.
2. Check that the control table exists and the service account can read it.
3. Check the configured table and dataset names.
4. Do not clear the data cache merely to remove the warning.
5. Restore the control query, then wait for the next poll or use an authorized
   manual refresh.

## Failed refresh

The refresh is intended to be all-or-nothing. If one eager table fails:

1. The previous complete RAM generation remains active.
2. `refresh_error` identifies the failed table.
3. Derived caches should remain associated with the old generation.
4. Correct the source or connectivity issue.
5. Retry after the control token remains or becomes eligible for refresh.

Do not mix a manually loaded table from a newer ETL run with dimensions from an
older generation.

## SQLite failure or corruption

### Write or activation failure

The RAM generation may still be usable, but `persistent_error` must be visible.
Check:

- directory permissions and free disk space;
- file-system quotas;
- whether another process holds the file;
- the temporary file cleanup state;
- security policy for the cache directory.

Correct the storage problem and trigger a complete refresh. Do not copy a
partially written SQLite file over the active snapshot.

### Restore failure

If integrity, metadata, table names, columns or row counts fail validation:

1. Preserve the file for forensic analysis if policy permits.
2. Do not use it as an active generation.
3. Check whether Netezza is available for a full reload.
4. Move the corrupt file to approved quarantine storage if authorized.
5. Rebuild the snapshot from a complete published generation.

Production must define encryption, quarantine, retention and evidence rules for
this operation.

## Restart and rollback

For a normal restart, the process should restore a matching snapshot without
reading every eager table. If the control token is newer, a full refresh is
expected.

Rollback must specify both application code and data generation. Reverting code
alone does not make a newer data generation compatible. Use the ETL/load ID,
snapshot metadata and release ID together when investigating a rollback.

## Incident escalation template

Record:

```text
Incident ID:
Start/end time:
Application release:
Dataset name/version/load ID:
Refreshed version:
Observed stale/error fields:
User/business impact:
Actions taken:
Recovery verification:
Follow-up owner:
```
