# ADR 0002 — ETL publication and cache generation

> Status: Accepted
> Date: 2026-09-06
> Owner role: data engineering
> Applies to: demo and production pattern

## Context

Reports must not mix dimensions and facts from different ETL loads. A large
table reload on every request is also unacceptable.

## Decision

The ETL publishes a `PUBLISHED` row in `MIS_CONTROL_DATASET_LOAD` only after a
complete validated dataset is available. The identity is
`(dataset_name, version_no, load_id)`. The application reads the control table
directly, refreshes all eager tables as one generation, and keeps the previous
complete generation on failure.

## Consequences

Control polling is cheap and user requests do not scan Netezza repeatedly.
Publication ordering becomes a correctness requirement shared with ETL.

## Current demo implementation

`CacheCoordinator`, `DatasetVersion` and `CachedMISRepository` implement the
generation boundary. ETL validation is trusted rather than duplicated in the
application.

## Production adaptation

Data engineering must decide whether the control table remains the contract or
is replaced by an orchestration/event interface, and whether a per-table
manifest is required.

## Acceptance evidence

- publication-last integration test;
- reconciliation and row-count evidence;
- failed-refresh test showing the old generation remains active.
