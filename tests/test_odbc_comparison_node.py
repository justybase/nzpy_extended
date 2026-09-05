"""Extended parity tests against the independent JustyBase Node driver."""

import pytest

from reference_driver import ReferenceConnection, compare_reference_rows
from test_odbc_comparison import _nzpy_conn
from odbc_queries_node import QUERIES_NODE

pytestmark = [pytest.mark.full, pytest.mark.reference_node]

_SHARED_REFERENCE = None

_OPTIONAL_RELATIONS = (
    "JUST_DATA.ADMIN.CUSTOMERADDRESS",
    "JUST_DATA.ADMIN.CUSTOMERDATA",
)


def _skip_missing_fixture_relations(sql: str) -> None:
    sql_upper = sql.upper()
    for relation in _OPTIONAL_RELATIONS:
        if relation in sql_upper:
            pytest.skip(f"Optional fixture relation is unavailable: {relation}")


def _get_shared_reference():
    global _SHARED_REFERENCE
    if _SHARED_REFERENCE is None:
        _SHARED_REFERENCE = ReferenceConnection()
    return _SHARED_REFERENCE


@pytest.fixture(scope="module", autouse=True)
def _close_reference_driver():
    yield
    if _SHARED_REFERENCE is not None:
        _SHARED_REFERENCE.close()


@pytest.mark.parametrize("sql", QUERIES_NODE)
@pytest.mark.asyncio
@pytest.mark.timeout(600)  # 10 min timeout per query
async def test_node_query_matches_reference_driver(sql):
    _skip_missing_fixture_relations(sql)
    reference_con = _get_shared_reference()
    nzpy_con = await _nzpy_conn()
    nz_cur = nzpy_con.cursor()
    reference_cur = reference_con.cursor()
    try:
        await nz_cur.execute(sql)
        nz_rows = await nz_cur.fetchall()
        reference_cur.execute(sql)
        reference_rows = reference_cur.fetchall()
        compare_reference_rows(nz_rows, reference_rows, sql, reference_cur.description)
    finally:
        await nz_cur.close()
        reference_cur.close()
        await nzpy_con.close()
