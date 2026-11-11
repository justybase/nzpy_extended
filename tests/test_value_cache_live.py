"""Live checks for per-column immutable-value deduplication."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal

import pytest

pytestmark = pytest.mark.smoke

# The cache only shares values whose Python object is immutable. Integers,
# floats and booleans are returned as primitives and are not deduplicated.
SHARED_TYPES = (str, Decimal, date, time)

QUERIES = [
    pytest.param(
        """
        SELECT
            T1.EVENT_ID
            , T1.CUSTOMER_ID
            , T1.EVENT_TS
            , T1.AMOUNT
            , T1.TAX
            , T1.QUANTITY
            , T1.STATUS
            , T1.SKU
            , T1.NOTE
        FROM JUST_DATA.PERF.EVENTS_400K T1
        """,
        id="EVENTS_400K",
    ),
    pytest.param(
        """
        SELECT
            T1.PRODUCTKEY
            , T1.DATEKEY
            , T1.MOVEMENTDATE
            , T1.UNITCOST
            , T1.UNITSIN
            , T1.UNITSOUT
            , T1.UNITSBALANCE
        FROM JUST_DATA.ADMIN.FACTPRODUCTINVENTORY T1
        """,
        id="FACTPRODUCTINVENTORY",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", QUERIES)
async def test_repeated_values_are_shared(cursor, sql: str) -> None:
    await cursor.execute(sql)
    seen: list[dict[object, object]] = [{} for _ in range(len(cursor.description or []))]
    shared = 0
    rows = 0
    async for row in cursor:
        rows += 1
        for index, value in enumerate(row):
            if value is None or not isinstance(value, SHARED_TYPES):
                continue
            column = seen[index]
            previous = column.get(value)
            if previous is None:
                column[value] = value
            elif value is previous:
                shared += 1
            # A value may repeat after the bounded per-column cache is full;
            # such a repeat is decoded again rather than shared.
    assert rows > 0
    assert shared > 0, "no repeated immutable value was shared"
