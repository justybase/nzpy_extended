"""Every metadata filter must reach the SQL as an escaped literal (no injection)."""

from typing import Any

import pytest

from nzpy_extended._metadata_api import ConnectionMetadataProvider

pytestmark = [pytest.mark.full, pytest.mark.unit]

HOSTILE = "x' OR '1'='1"
ESCAPED = "x'' OR ''1''=''1"


class _Recorder(ConnectionMetadataProvider):
    def __init__(self) -> None:
        super().__init__(None)  # type: ignore[arg-type]
        self.sql: list[str] = []

    async def _query(self, sql: str) -> list[tuple[Any, ...]]:
        self.sql.append(sql)
        return []

    async def _query_dicts(self, sql: str) -> list[dict[str, Any]]:
        self.sql.append(sql)
        return []


CALLS = [
    ("get_tables", {"schema": HOSTILE}),
    ("get_tables", {"table_pattern": HOSTILE}),
    ("get_views", {"schema": HOSTILE}),
    ("get_views", {"view_pattern": HOSTILE}),
    ("get_table_sizes", {"schema": HOSTILE}),
    ("get_table_sizes", {"table_pattern": HOSTILE}),
    ("get_procedures", {"schema": HOSTILE}),
    ("get_procedures", {"proc_pattern": HOSTILE}),
    ("get_sequences", {"schema": HOSTILE}),
    ("get_synonyms", {"schema": HOSTILE}),
    ("get_query_history", {"username": HOSTILE}),
]


@pytest.mark.parametrize("method, kwargs", CALLS, ids=[f"{m}-{next(iter(k))}" for m, k in CALLS])
async def test_filter_value_is_escaped(method: str, kwargs: dict[str, Any]) -> None:
    provider = _Recorder()
    await getattr(provider, method)(**kwargs)
    assert provider.sql, "no query was issued"
    sql = provider.sql[-1]
    assert ESCAPED in sql
    # The unescaped form only ever appears inside the escaped one.
    assert sql.count(HOSTILE) == 0
