# ADR 0005 — backend scope authorization

> Status: Accepted
> Date: 2026-09-06
> Owner role: security architecture
> Applies to: demo and production pattern

## Context

Sales-network reports contain data whose visibility depends on region, branch
or advisor scope. Frontend filtering is insufficient.

## Decision

Authentication is resolved before protected requests. Scope is enforced in
backend services and repository wrappers. Report and derived-cache keys include
the effective user scope where the result can differ.

## Consequences

Every new endpoint and export must receive the authenticated user and apply the
same policy. A UI-only visibility change is never an authorization change.

## Current demo implementation

`SessionUser`, `ScopedMISRepository`, service checks and route dependencies
implement full, region, branch and advisor scope.

## Production adaptation

Security must approve entitlement source, deny-by-default behaviour, access
reviews, emergency access and audit events.

## Acceptance evidence

- positive and negative scope tests;
- export and drill-down authorization tests;
- access-review and audit-control evidence.
