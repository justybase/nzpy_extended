# API conventions

> Document type: API contract companion
> Status: current demo contract with production decisions marked
> Source: FastAPI routes, Pydantic schemas and route tests

## Authentication

- The root page and static assets are public.
- Data endpoints require the `HttpOnly` authentication cookie.
- Missing, expired or invalid credentials return `401`.
- Scope is enforced in backend services and repositories.
- The browser must not be treated as an authorization boundary.

Production must replace the demo identity provider, define token validation,
key rotation, revocation, CSRF and session timeout controls.

## Response categories

| Status | Current meaning |
|---|---|
| `200` | Successful JSON response or file preparation |
| `204` | Successful logout |
| `400` | Invalid route-specific technical value, such as format or sort direction |
| `401` | Missing or invalid authentication |
| `403` | Authenticated user lacks capability or scope |
| `404` | Unknown report/entity or no export rows where documented |
| `422` | Invalid reporting period, `as_of`, attribution or request body |
| `503` | Data not ready or Netezza/service dependency unavailable |

Most errors currently use FastAPI's `{"detail": "..."}` shape. A production
deployment must decide whether to introduce a stable error envelope with an
error code, correlation ID, retry classification and safe user message.

## Reporting parameters

- `from` and `to` use `YYYY-MM` for ordinary monthly reports.
- `as_of` uses `YYYY-MM-DD` for point-in-time reporting.
- `attribution` is `historical` or `current`.
- `dim` is report-specific and is documented by the report registry.
- `page` is one-based; ledger `page_size` is bounded by the route.
- Export formats are `xlsx` and `xlsb` where supported.

Exact accepted values and response models are generated in `openapi.json`.

## Freshness metadata

Report responses contain `freshness`. Status/meta responses contain cache and
control-table fields. Consumers must distinguish:

- `dataset_version`: latest published control version;
- `refreshed_version`: complete generation currently in RAM;
- `stale`: freshness warning;
- `control_error`: control query failure;
- `refresh_error`: incomplete data refresh;
- `persistent_error`: SQLite persistence problem.

Consumers must not treat `dataset_version` alone as proof that the newer data
is being served.

## File responses

Exports return a file response and are subject to authorization, row limits,
temporary-file cleanup and production data-loss-prevention controls. A bank
deployment must decide whether exports require separate audit events and
watermarking.
