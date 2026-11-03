"""Regression tests for the 0.9 driver-semantics fixes, against the offline mock.

Each test pins one behaviour that used to fail silently or poison a session:
parameter errors, NUL characters, transaction tracking, discarded cursors,
logging of values, unknown messages, handshake stalls and the sync runner.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import sys
import threading
import time

import pytest

import nzpy_extended as nz
from nzpy_extended._runner import runner
from nzpy_extended.exceptions import (
    DataError,
    InterfaceError,
    InternalError,
    OperationalError,
    ProgrammingError,
)
from nzpy_extended.utils import convert_paramstyle, render_prepared_statement
from tests.mock_netezza import (
    Column,
    MockNetezzaServer,
    ServerSession,
    command_complete,
    default_responder,
    error_response,
    message,
    ready,
    text_result,
)

pytestmark = [pytest.mark.full, pytest.mark.unit]

INT4 = 23

ONE_ROW = text_result([Column("A", INT4, 4)], [[b"1"]])


def _recording_responder(seen: list[str], extra: dict[str, bytes] | None = None):
    responses = {"SELECT 1": ONE_ROW + ready(), "SELECT 2": text_result(
        [Column("A", INT4, 4)], [[b"2"], [b"3"]]) + ready()}
    responses.update(extra or {})

    def respond(sql: str, sess: ServerSession) -> None:
        seen.append(sql)
        if sql in responses:
            sess.reply(responses[sql])
            return
        upper = sql.strip().upper()
        if upper in ("BEGIN", "COMMIT", "ROLLBACK"):
            sess.reply(command_complete(upper.encode()), ready())
            return
        if upper.startswith("INSERT"):
            sess.reply(command_complete(b"INSERT 0 1"), ready())
            return
        default_responder(sql, sess)

    return respond


# -- parameters -------------------------------------------------------------

async def test_parameter_count_mismatch_raises_and_session_stays_usable() -> None:
    seen: list[str] = []
    with MockNetezzaServer(responder=_recording_responder(seen)) as server:
        conn = await nz.connect(**server.connect_kwargs())
        cur = conn.cursor()
        with pytest.raises(ProgrammingError, match="parameter"):
            await cur.execute("SELECT ?, ?", (1,))
        with pytest.raises(ProgrammingError, match="uses 1 parameter"):
            await cur.execute("SELECT ?", (1, 2, 3))
        with pytest.raises(ProgrammingError, match="uses 0 parameter"):
            await cur.execute("SELECT 1", (1,))
        started = time.monotonic()
        await cur.execute("SELECT 1")
        assert await cur.fetchall() == [[1]]
        assert time.monotonic() - started < 1.5, "the next statement waited for a drain"
        await conn.close()
        server.assert_no_handler_errors()
    assert not any("?" in sql for sql in seen), "a bad statement reached the server"


async def test_nul_in_a_text_parameter_is_rejected_before_sending() -> None:
    seen: list[str] = []
    with MockNetezzaServer(responder=_recording_responder(seen)) as server:
        conn = await nz.connect(**server.connect_kwargs())
        cur = conn.cursor()
        with pytest.raises(DataError, match="NUL"):
            await cur.execute("SELECT ?", ("a\x00b",))
        with pytest.raises(ProgrammingError, match="NUL"):
            await cur.execute("SELECT 'a\x00b'")
        await cur.execute("SELECT 1")
        assert await cur.fetchall() == [[1]]
        await conn.close()
        server.assert_no_handler_errors()


def test_block_comments_hide_placeholders_and_quotes() -> None:
    statement, make_args = convert_paramstyle("qmark", "SELECT /* it's ? */ ? AS a")
    assert statement == "SELECT /* it's ? */ $1 AS a"
    assert render_prepared_statement(statement, make_args((5,))) == (
        "SELECT /* it's ? */ 5 AS a", 1)


# -- transactions -----------------------------------------------------------

async def test_transaction_state_follows_command_tags() -> None:
    with MockNetezzaServer(responder=_recording_responder([])) as server:
        conn = await nz.connect(**server.connect_kwargs())
        cur = conn.cursor()
        await cur.execute("BEGIN")
        assert conn.in_transaction
        await cur.execute("ROLLBACK")
        assert not conn.in_transaction
        await cur.execute("BEGIN")
        await cur.execute("COMMIT")
        assert not conn.in_transaction
        await conn.close()


async def test_statement_in_aborted_transaction_raises_instead_of_returning_nothing() -> None:
    aborted = command_complete(b"*ABORT STATE*") + ready()
    responses = {"SELECT 1/0": error_response("Divide by 0") + ready(),
                 "SELECT COUNT(*) FROM T": aborted}
    with MockNetezzaServer(responder=_recording_responder([], responses)) as server:
        conn = await nz.connect(**server.connect_kwargs())
        cur = conn.cursor()
        await cur.execute("BEGIN")
        with pytest.raises(DataError, match="Divide by 0"):
            await cur.execute("SELECT 1/0")
        with pytest.raises(InternalError, match="aborted"):
            await cur.execute("SELECT COUNT(*) FROM T")
        assert conn.in_transaction
        with pytest.raises(InternalError, match="no changes were saved"):
            await conn.commit()
        assert not conn.in_transaction
        # A new transaction starts clean.
        await cur.execute("BEGIN")
        await cur.execute("COMMIT")
        await conn.close()
        server.assert_no_handler_errors()


async def test_pool_rolls_back_a_transaction_begun_by_the_caller() -> None:
    seen: list[str] = []
    with MockNetezzaServer(responder=_recording_responder(seen)) as server:
        pool = nz.NzPool(min_size=0, max_size=1, ping_query=None, **server.connect_kwargs())
        conn = await pool.acquire()
        assert conn.autocommit
        await conn.cursor().execute("BEGIN")
        await conn.cursor().execute("INSERT INTO T VALUES (1)")
        await pool.release(conn)
        again = await pool.acquire()
        assert again is conn
        assert not again.in_transaction
        await pool.release(again)
        await pool.close_all()
        server.assert_no_handler_errors()
    assert seen[-1].strip().lower() == "rollback"


# -- cursors ----------------------------------------------------------------

async def test_fetch_from_a_cursor_whose_rows_were_discarded_raises() -> None:
    with MockNetezzaServer(responder=_recording_responder([])) as server:
        conn = await nz.connect(**server.connect_kwargs())
        first, second = conn.cursor(), conn.cursor()
        await first.execute("SELECT 2")
        assert await first.fetchone() == [2]
        await second.execute("SELECT 1")
        assert await second.fetchall() == [[1]]
        with pytest.raises(InterfaceError, match="discarded"):
            await first.fetchall()
        # Executing again clears the condition.
        await first.execute("SELECT 1")
        assert await first.fetchall() == [[1]]
        await conn.close()


async def test_fully_fetched_cursor_is_not_marked_discarded() -> None:
    with MockNetezzaServer(responder=_recording_responder([])) as server:
        conn = await nz.connect(**server.connect_kwargs())
        first, second = conn.cursor(), conn.cursor()
        await first.execute("SELECT 1")
        assert await first.fetchall() == [[1]]
        await second.execute("SELECT 1")
        assert await first.fetchall() == []
        await conn.close()


# -- logging ----------------------------------------------------------------

async def test_parameter_values_never_reach_the_log(caplog: pytest.LogCaptureFixture) -> None:
    secret = "p@ss-SECRET-42"
    responses = {f"SELECT '{secret}'": ONE_ROW + ready()}
    with MockNetezzaServer(responder=_recording_responder([], responses)) as server:
        with caplog.at_level(logging.DEBUG, logger="nzpy_extended"):
            conn = await nz.connect(**server.connect_kwargs())
            cur = conn.cursor()
            await cur.execute("SELECT ?", (secret,))
            await cur.fetchall()
            await conn.close()
    assert caplog.records, "debug logging was not captured"
    assert all(secret not in record.getMessage() for record in caplog.records)


async def test_default_connection_does_not_force_a_log_level() -> None:
    with MockNetezzaServer() as server:
        conn = await nz.connect(**server.connect_kwargs(database="LOGLEVEL_DEFAULT"))
        assert conn.log.level == logging.NOTSET
        await conn.close()


# -- framing ----------------------------------------------------------------

async def test_unknown_backend_message_closes_the_connection() -> None:
    responses = {"WEIRD": message(b"Q", b"??") + ready()}
    with MockNetezzaServer(responder=_recording_responder([], responses)) as server:
        conn = await nz.connect(**server.connect_kwargs())
        with pytest.raises(InterfaceError, match="Unknown backend message"):
            await conn.cursor().execute("WEIRD")
        assert not conn._is_reusable()
        await conn.close()


async def test_handshake_stall_is_bounded_by_connect_timeout() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    accepted: list[socket.socket] = []

    def accept() -> None:
        conn, _ = listener.accept()
        accepted.append(conn)  # never answers

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        with pytest.raises(OperationalError, match="handshake did not complete"):
            await nz.connect(user="u", password="p", host="127.0.0.1",
                             port=listener.getsockname()[1], database="d",
                             connect_timeout=1.0)
        assert time.monotonic() - started < 5
    finally:
        for sock in accepted:
            sock.close()
        listener.close()


# -- sync runner ------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX SIGINT delivery")
def test_interrupted_sync_call_cancels_its_coroutine() -> None:
    cancelled = threading.Event()

    async def slow() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    # A real SIGINT (as from Ctrl+C) wakes the blocked wait; interrupt_main() would not.
    timer = threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGINT))
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            runner.run(slow())
    finally:
        timer.cancel()
    assert cancelled.wait(5), "the coroutine kept running after the interrupt"

