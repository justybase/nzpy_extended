# ADR 0007 — API contract and versioning

> Status: Production decision required
> Date: 2026-09-06
> Owner role: API architecture
> Applies to: current API and future production consumers

## Context

FastAPI can generate an accurate OpenAPI document, but consumers also need a
change policy, error semantics and compatibility guarantees.

## Decision

The current application generates OpenAPI from routes and Pydantic schemas. A
committed snapshot is checked for drift. Breaking changes require an explicit
versioning or compatibility decision.

## Consequences

The generated contract exposes current behaviour without maintaining a second
handwritten API description. A production owner still must define lifecycle,
deprecation, error envelope, correlation IDs and consumer notification.

## Current demo implementation

The API uses unversioned `/api` paths and mostly FastAPI `detail` errors.

## Production adaptation

Choose `/api/v1` or an equivalent policy, standardize errors and define a
compatibility test for reports, exports, pagination and freshness fields.

## Acceptance evidence

- generated OpenAPI snapshot;
- drift test;
- approved breaking-change and deprecation policy.
