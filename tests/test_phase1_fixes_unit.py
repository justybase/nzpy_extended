"""Regression tests for the Phase 1 correctness fixes (no database needed).

* the sync runner does not mask errors raised by the coroutine itself;
* executemany keeps the DB-API error class and records the failing index;
* NaN and infinity parameters become valid Netezza casts;
* Netezza has no E'' escape strings: a backslash is an ordinary character;
* a password sent over a plaintext session is logged as a warning.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

import nzpy_extended as nz
from nzpy_extended._runner import runner  # pyright: ignore[reportPrivateUsage]
from nzpy_extended.cursor import Cursor
from nzpy_extended.exceptions import IntegrityError, NotSupportedError, ProgrammingError
from nzpy_extended.utils import _sql_literal  # pyright: ignore[reportPrivateUsage]
from nzpy_extended.utils import convert_paramstyle, render_prepared_statement
from tests.mock_netezza import MockNetezzaServer, make_self_signed_context

pytestmark = pytest.mark.unit


# -- 1. sync runner ----------------------------------------------------------

def test_runner_propagates_runtime_error_from_the_coroutine_unchanged() -> None:
    async def fails() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("original failure")

    with pytest.raises(RuntimeError, match="^original failure$"):
        runner.run(fails())


def test_runner_is_usable_after_a_coroutine_error() -> None:
    async def fails() -> None:
        raise RuntimeError("boom")

    async def ok() -> int:
        return 42

    with pytest.raises(RuntimeError):
        runner.run(fails())
    assert runner.run(ok()) == 42


def test_runner_restarts_when_its_loop_was_stopped() -> None:
    async def ok() -> int:
        return 7

    runner.close()
    assert runner.run(ok()) == 7


# -- 2. executemany error class ---------------------------------------------

def _cursor_with_scripted_execute(fail_at: int, error: Exception) -> Cursor:
    cur = Cursor(SimpleNamespace())  # type: ignore[arg-type]
    calls = {"n": 0}

    async def clear() -> None:
        return None

    async def execute(operation: str, parameters: Any = None) -> Cursor:
        calls["n"] += 1
        if calls["n"] == fail_at:
            raise error
        cur.row_count = 1
        return cur

    cur.clear = clear  # type: ignore[method-assign]
    cur.execute = execute  # type: ignore[method-assign]
    return cur


def test_executemany_keeps_the_db_api_error_class_and_records_the_index() -> None:
    cur = _cursor_with_scripted_execute(2, IntegrityError("duplicate key"))

    async def run() -> None:
        with pytest.raises(IntegrityError, match="duplicate key") as info:
            await cur.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
        assert info.value.__notes__ == ["executemany failed at param set 2/3"]
        # str(e) is unchanged, so existing message matching keeps working.
        assert str(info.value) == "duplicate key"
        assert cur.row_count == 1

    asyncio.run(run())


def test_executemany_wraps_non_db_errors_in_programming_error() -> None:
    cur = _cursor_with_scripted_execute(1, ValueError("bad value"))

    async def run() -> None:
        with pytest.raises(ProgrammingError, match="param set 1/1: bad value"):
            await cur.executemany("INSERT INTO t VALUES (?)", [(object(),)])

    asyncio.run(run())


# -- 3. NaN / infinity literals ---------------------------------------------

def test_nan_and_infinity_render_as_double_precision_casts() -> None:
    assert _sql_literal(float("nan")) == "CAST('NaN' AS DOUBLE PRECISION)"
    assert _sql_literal(float("inf")) == "CAST('Infinity' AS DOUBLE PRECISION)"
    assert _sql_literal(float("-inf")) == "CAST('-Infinity' AS DOUBLE PRECISION)"


def test_finite_numbers_keep_their_plain_literal_form() -> None:
    assert _sql_literal(1.5) == "1.5"
    assert _sql_literal(1e-5) == "1e-05"
    assert _sql_literal(42) == "42"
    assert _sql_literal(Decimal("1.25")) == "1.25"


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_decimal_is_rejected_client_side(value: Decimal) -> None:
    with pytest.raises(NotSupportedError, match="NUMERIC"):
        _sql_literal(value)


def test_nan_parameter_is_substituted_into_the_statement() -> None:
    # The renderer receives the raw Python value, as Connection.Prepare passes it.
    sql, count = render_prepared_statement("SELECT $1", (float("nan"),))
    assert count == 1
    assert sql == "SELECT CAST('NaN' AS DOUBLE PRECISION)"


# -- 4. no E'' escape strings on Netezza --------------------------------------

def test_backslash_before_closing_quote_does_not_hide_later_parameters() -> None:
    # Netezza: the literal 'abc\\' ends at its second quote, so the ? after it is a parameter.
    sql = convert_paramstyle("qmark", "SELECT 'abc\\\\', ?")[0]
    assert sql == "SELECT 'abc\\\\', $1"
    rendered, expected = render_prepared_statement(sql, ("X",))
    assert expected == 1
    assert rendered == "SELECT 'abc\\\\', 'X'"


def test_e_prefixed_string_is_an_ordinary_string_for_the_placeholder_scanner() -> None:
    sql = convert_paramstyle("qmark", "SELECT E'abc\\\\', ?, 'it''s', ?")[0]
    assert sql == "SELECT E'abc\\\\', $1, 'it''s', $2"


def test_question_mark_inside_a_literal_is_not_a_parameter() -> None:
    sql = convert_paramstyle("qmark", "SELECT 'a?b\\', ?")[0]
    assert sql == "SELECT 'a?b\\', $1"


# -- 5. plaintext password warning ---------------------------------------------

@pytest.mark.asyncio
async def test_password_over_plaintext_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with MockNetezzaServer(auth="password") as server:
        with caplog.at_level(logging.WARNING, logger="nzpy_extended"):
            conn = await nz.connect(**server.connect_kwargs())
            await conn.close()
    assert any("UNENCRYPTED" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_password_over_tls_does_not_log_the_plaintext_warning(
    tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    if shutil.which("openssl") is None:
        pytest.skip("openssl executable required for the TLS mock certificate")
    server_context, cert = make_self_signed_context(tmp_path)
    with MockNetezzaServer(auth="password", tls_context=server_context) as server:
        with caplog.at_level(logging.WARNING, logger="nzpy_extended"):
            conn = await nz.connect(**server.connect_kwargs(
                securityLevel=1,
                ssl={"ca_certs": str(cert), "server_hostname": "localhost"},
            ))
            await conn.close()
    assert not any("UNENCRYPTED" in r.getMessage() for r in caplog.records)
