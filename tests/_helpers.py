"""Shared helpers for integration and differential tests."""

from __future__ import annotations

import datetime
import decimal
from typing import Any


def normalize(val: Any) -> Any:
    """Keep exact values; normalization must not conceal conversion defects."""
    return val


def compare_rows(nz_rows: list[Any], reference_rows: list[Any], query: str) -> None:
    import math

    assert len(nz_rows) == len(reference_rows), f"Row count mismatch for {query!r}"
    for row_idx, (n_row, reference_row) in enumerate(zip(nz_rows, reference_rows)):
        assert len(n_row) == len(reference_row), f"Column count mismatch for {query!r}"
        for col_idx, (n_val, reference_value) in enumerate(zip(n_row, reference_row)):
            if isinstance(n_val, float) and isinstance(reference_value, (float, decimal.Decimal)):
                equal = math.isclose(n_val, float(reference_value), rel_tol=1e-7, abs_tol=1e-12)
            else:
                equal = n_val == reference_value
                if isinstance(n_val, bool) != isinstance(reference_value, bool):
                    equal = False
            assert equal, (
                f"{query!r}: row {row_idx} col {col_idx}: "
                f"nzpy={n_val!r} reference={reference_value!r}"
            )
