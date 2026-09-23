"""Database-free cursor result-shape regressions."""

from __future__ import annotations

from types import SimpleNamespace
from typing import AsyncIterator

import pytest

from nzpy_extended.cursor import Cursor

pytestmark = pytest.mark.unit


async def _ready_generator() -> AsyncIterator[str]:
    yield "DATA_BATCH"
    yield "READY_FOR_QUERY"


def _cursor() -> Cursor:
    conn = SimpleNamespace(_closed=False, _stream=None)
    return Cursor(conn)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fetchall_returns_row_lists_and_drains_cached_rows() -> None:
    cur = _cursor()
    cur.ps = {"row_desc": [{"name": b"id"}]}
    cur.cached_rows.extend([[1, "first"], [2, None]])
    cur.generator = _ready_generator()

    rows = await cur.fetchall()

    assert rows == [[1, "first"], [2, None]]
    assert all(type(row) is list for row in rows)
    assert not cur.cached_rows
    assert cur.generator is None


@pytest.mark.asyncio
async def test_fetchmany_returns_row_lists_in_order() -> None:
    cur = _cursor()
    cur.cached_rows.extend([[3], [4]])

    rows = await cur.fetchmany(2)

    assert rows == [[3], [4]]
    assert all(type(row) is list for row in rows)
    assert not cur.cached_rows
