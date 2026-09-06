# Architecture decision records

> Document type: ADR index
> Status: maintained
> Production adaptation: bank-specific decisions must be recorded before go-live

ADRs record decisions that affect multiple modules, data correctness, security
or operations. They complement implementation documentation; they do not
replace tests or production approval.

## Status values

- `Accepted` — applies to the current example;
- `Demo constraint` — intentionally simplified for this repository;
- `Production decision required` — the example does not choose the target
  environment;
- `Superseded` — retained for history only.

## Records

| ADR | Decision |
|---|---|
| [0001](0001-documentation-and-contract-sources.md) | Documentation and contract sources |
| [0002](0002-etl-publication-and-cache-generation.md) | ETL publication and cache generation |
| [0003](0003-local-sqlite-snapshot.md) | Local SQLite snapshot boundary |
| [0004](0004-point-in-time-quality-gates.md) | Point-in-time quality gates and attribution |
| [0005](0005-backend-scope-authorization.md) | Backend scope authorization |
| [0006](0006-demo-identity-boundary.md) | Demo identity boundary |
| [0007](0007-api-contract-and-versioning.md) | API contract and versioning |

## Creating a new ADR

Copy [`template.md`](template.md), use the next number, and update this index.
An ADR is required when a change alters a public contract, data-generation
boundary, authorization rule, deployment assumption, security control or
operational recovery procedure.
