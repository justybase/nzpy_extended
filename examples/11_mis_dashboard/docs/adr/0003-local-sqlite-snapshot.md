# ADR 0003 — local SQLite snapshot

> Status: Demo constraint
> Date: 2026-09-06
> Owner role: platform architecture
> Applies to: current example

## Context

RAM is fast for report serving, but a restart otherwise requires a complete
Netezza reload. The example needs a durable local recovery copy.

## Decision

The application keeps RAM as the normal read layer and stages a complete local
SQLite snapshot for restart recovery. The new file is activated atomically.
Lazy performance slices are associated with the active generation ID.

## Consequences

Restart can avoid a large source read. The snapshot contains the full unmasked
cache, requires a writable configured directory, and is not a distributed
cache. Exclusive ownership of that directory by the application account is
not required by this example.

## Current demo implementation

`SQLiteSnapshotStore` uses a temporary file, integrity checks and atomic
replacement. The supported topology is one process and one host.

## Production adaptation

The platform and security owners must decide whether to encrypt the snapshot,
move it to a shared/cache service, remove it, or isolate it per worker. Blocking
SQLite I/O must be measured and moved off the event loop if required.

## Acceptance evidence

- restore, corruption and failed-staging tests;
- permission and encryption review;
- restart, multi-worker and capacity tests for the chosen topology.
