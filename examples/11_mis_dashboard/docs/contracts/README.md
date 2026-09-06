# Contracts

> Document type: machine-readable contract index
> Status: maintained
> Production adaptation: API and data governance review required

## Files

| File | Source | Verification |
|---|---|---|
| `openapi.json` | FastAPI route and Pydantic definitions | Generated snapshot comparison |
| `data-contract.json` | Table configuration, seed schema and data dictionary | Table-role and required-field checks |
| `api-conventions.md` | Current route behaviour plus production target decisions | Human review and API tests |

## Change policy

- `openapi.json` is generated; do not hand-edit it.
- A route or response-model change must update the generated snapshot and the
  related API documentation in the same change.
- A breaking response or parameter change requires an ADR and an API version
  or approved compatibility plan.
- `data-contract.json` describes the reporting contract, not every physical
  database implementation detail. Full columns and relationships remain in
  `docs/data_dictionary.md`.
- A table grain or metric-definition change requires a business-owner review.

## Validation commands

```bash
python tools/export_openapi.py --check
python tools/verify_documentation.py
python -m pytest tests/test_documentation_contracts.py -q
```
