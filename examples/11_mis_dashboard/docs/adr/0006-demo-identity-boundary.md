# ADR 0006 — demo identity boundary

> Status: Demo constraint
> Date: 2026-09-06
> Owner role: security architecture
> Applies to: current example only

## Context

The example must run without a corporate identity provider, but embedding a
local identity directory and public credentials is not acceptable in a bank.

## Decision

`FakeLDAPService` and seeded demo users are explicitly limited to the example.
The application boundary remains service-oriented so an approved identity
provider can replace the fake directory.

## Consequences

Local tests are deterministic, but the authentication flow is not evidence of
production SSO, key management, revocation or access governance.

## Current demo implementation

Demo passwords are documented, JWT defaults exist, and tester persona switching
is available for seeded users.

## Production adaptation

Replace the fake directory, remove demo credentials and default secrets, define
SSO claims, key rotation, session revocation, MFA and privileged-access rules.

## Acceptance evidence

- production identity integration test;
- secret scan and key-rotation test;
- privileged-access and audit review.
