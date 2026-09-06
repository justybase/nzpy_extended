# MIS Dashboard documentation index

> Document type: documentation index
> Status: maintained
> Applies to: `examples/11_mis_dashboard`
> Production adaptation: required before deployment
> Owner: application architecture

This directory separates the current runnable example from the decisions and
controls required by a real reporting platform.

## Start here

| Need | Document |
|---|---|
| Run the example and understand the visible features | [`../README.md`](../README.md) |
| Navigate the code and extend it safely | [`implementation-guide.md`](implementation-guide.md) |
| Understand tables, grain, keys and relationships | [`data_dictionary.md`](data_dictionary.md), [`erd.md`](erd.md) |
| Understand ETL publication and cache recovery | [`cache.md`](cache.md) |
| Understand authentication and row scope | [`authentication.md`](authentication.md) |
| Read API and data contracts | [`contracts/README.md`](contracts/README.md) |
| See architecture decisions | [`adr/README.md`](adr/README.md) |
| Prepare the example for a real bank environment | [`production-adaptation.md`](production-adaptation.md) |
| Operate, recover and troubleshoot the service | [`operations.md`](operations.md) |

## Document classes

Every document should state its class in the header:

| Class | Meaning |
|---|---|
| `CURRENT CONTRACT` | Behaviour currently guaranteed by code and tests |
| `DEMO ONLY` | Useful for the example but unsafe to promote unchanged |
| `PRODUCTION DECISION REQUIRED` | A bank-specific choice is still open |
| `PRODUCTION CONTROL` | A control expected before production approval |
| `NON-GOAL` | Explicitly outside this example's responsibility |

## Source-of-truth rules

- Runtime behaviour is defined by code and executable tests.
- Table grain and relationships are defined by `seed.py`,
  `sql/reporting_mart.sql` and `data_dictionary.md`.
- HTTP schemas are defined by Pydantic models and the generated OpenAPI
  contract.
- ETL publication and cache lifecycle are defined by `cache.md` and the cache
  coordinator tests.
- Production-specific values are not invented here. They belong in the
  adaptation matrix and the relevant ADR.

## Review cadence

Review documentation when any of the following changes:

- a route, response model or authentication rule;
- a table, key, grain or data-quality rule;
- cache generation, refresh or persistence behaviour;
- a role, scope or export capability;
- deployment topology, secret handling or operational ownership.

The documentation verifier checks required files, links, JSON contracts and
the OpenAPI snapshot. It does not replace business, security or architecture
review.
