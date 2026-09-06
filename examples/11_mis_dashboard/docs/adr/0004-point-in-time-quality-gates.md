# ADR 0004 — point-in-time quality gates and attribution

> Status: Accepted
> Date: 2026-09-06
> Owner role: reporting business owner and data quality
> Applies to: demo and production pattern

## Context

An `as_of` date is meaningful only when the reporting mart is reconciled to
that cut-off. Organization transfers also require an explicit attribution
choice.

## Decision

Point-in-time requests require an audit row with `status = PASS` and
`source_max_date = as_of`. Reports expose `historical` or `current` attribution
explicitly. The snapshot mart and audit table are separate contracts.

## Consequences

A row existing in the mart is not enough to make a reporting cut-off eligible.
Current attribution can restate historical activity; network totals and scoped
subtotals must be tested separately.

## Current demo implementation

`TemporalMISService`, `AsOfMISRepository` and the temporal routes implement the
quality gate, SCD2 assignment logic and standard period definitions.

## Production adaptation

The business owner must approve timezone, calendar, holiday, late-arriving-data
and restatement rules.

## Acceptance evidence

- boundary-date and failed-quality-gate tests;
- SCD2 transfer tests;
- business sign-off for DTD/MTD/PMTD/MoM/YTD/YoY definitions.
