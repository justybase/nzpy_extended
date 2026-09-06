/# ADR 0001 — documentation and contract sources

> Status: Accepted
> Date: 2026-09-06
> Owner role: application architecture
> Applies to: demo and production documentation

## Context

The repository contains code, tests, schema descriptions, API routes and
operational notes. Duplicated descriptions can drift and make a change unsafe.

## Decision

Executable tests and runtime code define current behaviour. Pydantic models and
generated OpenAPI define HTTP schemas. Seed/schema files and the data
dictionary define table grain. ADRs define cross-cutting decisions. Production
adaptation requirements remain in the adaptation matrix until an approved
environment-specific decision exists.

## Consequences

Documentation can be checked against code, while business and operational
decisions remain visible instead of being hidden in demo defaults.

## Current demo implementation

`docs/index.md`, `docs/contracts/`, `docs/adr/` and the documentation verifier
provide navigation and consistency checks.

## Production adaptation

The production repository should integrate contract and documentation checks
into its release pipeline and assign owners for each contract.

## Acceptance evidence

- `tools/verify_documentation.py` passes;
- the OpenAPI snapshot matches the application;
- the data contract matches configured table roles.
