"""Query-phase behaviour against the in-process Netezza mock (no database).

Covers result decoding, server errors, notices, fragmentation, cancel and
timeout recovery, framing faults, deferred value errors and pool probing on
real sockets.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import time

import pytest

import nzpy_extended as nz
from nzpy_extended import sync as sync_nzpy
from nzpy_extended.exceptions import DataError, Error, InterfaceError, OperationalError
from nzpy_extended.pool import NzPool, SyncPool
from tests.mock_netezza import (
    PADDING,
    Column,
    MockNetezzaServer,
    ServerSession,
    command_complete,
    data_row,
    default_responder,
    error_response,
    message,
    notice,
    read_wire_records,
    ready,
    row_description,
    text_result,
)

pytestmark = pytest.mark.unit

INT4 = 23
VARCHAR = 1043
TEXT = 25


def _ints_and_text_responder(sql: str, sess: ServerSession) -> None:
    if sql == "SELECT 1 AS A, 'abc' AS B, NULL AS C":
        sess.reply(
            text_result(
                [Column("A", INT4, 4), Column("B", VARCHAR, -1), Column("C", TEXT, -1)],
                [[b"1", b"abc", None], [b"2", b"xyz", None]],
            ),
            ready(),
        )
        return
    default_responder(sql, sess)


def _slow_responder(sql: str, sess: ServerSession) -> None:
    if sql == "SLOW":
        if sess.wait_cancel(5.0):
            sess.reply(error_response("Query was cancelled."), ready())
        return
    _ints_and_text_responder(sql, sess)


SELECT_ABC = "SELECT 1 AS A, 'abc' AS B, NULL AS C"


@pytest.mark.asyncio
async def test_rows_decode_to_python_values_with_nulls() -> None:
    with MockNetezzaServer(responder=_ints_and_text_responder) as server:
        conn = await nz.connect(**server.connect_kwargs())
        cursor = conn.cursor()
        await cursor.execute(SELECT_ABC)
        rows = await cursor.fetchall()
        await conn.close()
        server.assert_no_handler_errors()

    assert rows == [[1, "abc", None], [2, "xyz", None]]


def test_sync_api_reads_the_same_rows() -> None:
    with MockNetezzaServer(responder=_ints_and_text_responder) as server:
        conn = sync_nzpy.connect(**server.connect_kwargs())
        cursor = conn.cursor()
        cursor.execute(SELECT_ABC)
        rows = cursor.fetchall()
        conn.close()
        server.assert_no_handler_errors()

    assert rows == [[1, "abc", None], [2, "xyz", None]]


@pytest.mark.asyncio
async def test_multiple_result_sets_advance_with_nextset() -> None:
    def responder(sql: str, sess: ServerSession) -> None:
        if sql == "MULTI":
            sess.reply(
                row_description([Column("A", INT4, 4)]),
                data_row([b"1"]),
                command_complete(b"SELECT 1"),
                row_description([Column("B", VARCHAR, -1), Column("C", INT4, 4)]),
                data_row([b"x", b"2"]),
                command_complete(b"SELECT 1"),
                ready(),
            )
            return
        default_responder(sql, sess)

    with MockNetezzaServer(responder=responder) as server:
        conn = await nz.connect(**server.connect_kwargs())
        cursor = conn.cursor()
        await cursor.execute("MULTI")
        first = await cursor.fetchall()
        assert await cursor.nextset()
        second = await cursor.fetchall()
        await conn.close()
        server.assert_no_handler_errors()

    assert first == [[1]]
    assert second == [["x", 2]]


@pytest.mark.asyncio
async def test_server_error_is_raised_and_the_session_survives() -> None:
    def responder(sql: str, sess: ServerSession) -> None:
        if sql == "BROKEN":
            sess.reply(error_response("syntax error at or near FROM"), ready())
            return
        default_responder(sql, sess)

    with MockNetezzaServer(responder=responder) as server:
        conn = await nz.connect(**server.connect_kwargs())
        cursor = conn.cursor()
        with pytest.raises(Error, match="syntax error at or near FROM"):
            await cursor.execute("BROKEN")
        await cursor.execute("set after_error to 'yes'")
        await conn.close()
        server.assert_no_handler_errors()


@pytest.mark.asyncio
async def test_notices_are_collected_on_the_cursor() -> None:
    def responder(sql: str, sess: ServerSession) -> None:
        if sql == "NOISY":
            sess.reply(notice("ROLLBACK: no transaction in progress"),
                       text_result([Column("A", INT4, 4)], [[b"1"]]), ready())
            return
        default_responder(sql, sess)

    with MockNetezzaServer(responder=responder) as server:
        conn = await nz.connect(**server.connect_kwargs())
        cursor = conn.cursor()
        await cursor.execute("NOISY")
        rows = await cursor.fetchall()
        await conn.close()
        server.assert_no_handler_errors()

    assert rows == [[1]]
    assert any("no transaction in progress" in str(n) for n in cursor.notices)


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", [1, 3, 7, 4096])
async def test_fragmented_delivery_decodes_identically(chunk: int) -> None:
    with MockNetezzaServer(responder=_ints_and_text_responder, chunk=chunk) as server:
        conn = await nz.connect(**server.connect_kwargs())
        cursor = conn.cursor()
        await cursor.execute(SELECT_ABC)
        rows = await cursor.fetchall()
        await conn.close()
        server.assert_no_handler_errors()

    assert rows == [[1, "abc", None], [2, "xyz", None]]


@pytest.mark.asyncio
async def test_timeout_before_the_response_cancels_and_keeps_the_session() -> None:
    with MockNetezzaServer(responder=_slow_responder) as server:
        conn = await nz.connect(**server.connect_kwargs())
        cursor = conn.cursor()
        with pytest.raises(OperationalError):
            await cursor.execute("SLOW", timeout=0.3)

        # The cancel reached the server on its own socket, and the same session
        # is still usable once the cancelled response has been drained.
        assert server.cancels, "cancel request was not delivered"
        await cursor.execute(SELECT_ABC)
        rows = await cursor.fetchall()
        await conn.close()
        server.assert_no_handler_errors()

    assert rows == [[1, "abc", None], [2, "xyz", None]]


@pytest.mark.asyncio
async def test_stall_inside_a_message_body_retires_the_connection() -> None:
    def responder(sql: str, sess: ServerSession) -> None:
        if sql == "HALF":
            # A complete RowDescription, then a DataRow that promises 100 bytes
            # and delivers only part of them before stalling.
            sess.send(row_description([Column("A", INT4, 4)]))
            sess.send(b"D" + b"\x00" * 4 + struct.pack(">i", 100) + b"\x80" + b"\x00" * 8)
            sess.wait_cancel(5.0)
            return
        default_responder(sql, sess)

    with MockNetezzaServer(responder=responder) as server:
        conn = await nz.connect(**server.connect_kwargs())
        cursor = conn.cursor()
        with pytest.raises(OperationalError):
            await cursor.execute("HALF", timeout=0.3)
        assert conn._closed is True
        with pytest.raises(InterfaceError):
            await conn.cursor().execute(SELECT_ABC)
        server.assert_no_handler_errors()


@pytest.mark.asyncio
async def test_truncated_row_description_is_a_framing_error_that_closes() -> None:
    def responder(sql: str, sess: ServerSession) -> None:
        if sql == "BAD_T":
            # Claims 1000 fields in a payload that carries none of them.
            sess.reply(message(b"T", struct.pack(">H", 1000)), ready())
            return
        default_responder(sql, sess)

    with MockNetezzaServer(responder=responder) as server:
        conn = await nz.connect(**server.connect_kwargs())
        cursor = conn.cursor()
        with pytest.raises(InterfaceError):
            await cursor.execute("BAD_T")
        assert conn._closed is True
        server.assert_no_handler_errors()


@pytest.mark.asyncio
async def test_invalid_text_is_deferred_and_the_session_stays_in_sync() -> None:
    def responder(sql: str, sess: ServerSession) -> None:
        if sql == "BAD_UTF8":
            sess.reply(
                text_result([Column("A", TEXT, -1)], [[b"ok"], [b"\xff\xfe"], [b"tail"]]),
                ready(),
            )
            return
        _ints_and_text_responder(sql, sess)

    # Text is decoded with char_varchar_encoding; latin-1 accepts every byte, so use UTF-8.
    with MockNetezzaServer(responder=responder) as server:
        conn = await nz.connect(**server.connect_kwargs(char_varchar_encoding="utf-8"))
        cursor = conn.cursor()
        await cursor.execute("BAD_UTF8")
        with pytest.raises(DataError):
            await cursor.fetchall()

        # The same session keeps working: the value error did not desync it.
        follow = conn.cursor()
        await follow.execute(SELECT_ABC)
        assert await follow.fetchall() == [[1, "abc", None], [2, "xyz", None]]
        await conn.close()
        server.assert_no_handler_errors()


@pytest.mark.asyncio
async def test_padding_after_ready_for_query_is_skipped_between_statements() -> None:
    with MockNetezzaServer(responder=_ints_and_text_responder) as server:
        conn = await nz.connect(**server.connect_kwargs())
        for _ in range(3):
            cursor = conn.cursor()
            await cursor.execute(SELECT_ABC)
            assert len(await cursor.fetchall()) == 2
        await conn.close()
        server.assert_no_handler_errors()


@pytest.mark.asyncio
async def test_async_pool_replaces_a_connection_the_server_closed() -> None:
    with MockNetezzaServer(responder=_ints_and_text_responder) as server:
        pool = NzPool(min_size=1, max_size=2, ping_query=None, **server.connect_kwargs())
        try:
            conn = await pool.acquire()
            await pool.release(conn)

            # The server drops the idle session; the probe must notice before reuse.
            server.sessions[0].sock.shutdown(socket.SHUT_RDWR)
            await asyncio.sleep(0.05)

            conn = await pool.acquire()
            cursor = conn.cursor()
            await cursor.execute(SELECT_ABC)
            assert len(await cursor.fetchall()) == 2
            await pool.release(conn)
        finally:
            await pool.close_all()
        server.assert_no_handler_errors()

    assert len(server.connections) == 2


def _wait_until_idle_socket_sees_eof(pool: SyncPool, timeout: float = 5.0) -> None:
    """Wait until the server's close is visible on every idle client socket.

    The FIN from a shutdown that runs on another thread is not guaranteed to be
    visible at once (it is not on macOS), and the pool's health check only sees
    what has arrived. Waiting makes the test about replacement, not timing.
    """
    deadline = time.monotonic() + timeout
    while True:
        with pool._lock:
            idle = [pc.conn._conn for pc in pool._pool]
        if idle and all(not c._idle_socket_is_healthy() for c in idle):
            return
        if time.monotonic() >= deadline:
            raise AssertionError("the server's close never became visible on the idle socket")
        time.sleep(0.01)


def test_sync_pool_replaces_a_connection_the_server_closed() -> None:
    with MockNetezzaServer(responder=_ints_and_text_responder) as server:
        pool = SyncPool(min_size=1, max_size=2, ping_query=None, **server.connect_kwargs())
        try:
            conn = pool.acquire()
            pool.release(conn)

            server.sessions[0].sock.shutdown(socket.SHUT_RDWR)
            _wait_until_idle_socket_sees_eof(pool)
            conn = pool.acquire()
            cursor = conn.cursor()
            cursor.execute(SELECT_ABC)
            assert len(cursor.fetchall()) == 2
            pool.release(conn)
        finally:
            pool.close_all()
        server.assert_no_handler_errors()

    assert len(server.connections) == 2


def test_fixture_records_are_well_formed() -> None:
    # Guard the golden fixtures themselves: every record ends in ReadyForQuery.
    for name in ("text_types", "dbos_types", "null_matrix", "multi_result", "notice", "error"):
        records = read_wire_records(name)
        assert records, name
        for _, response in records:
            assert response[-5:-4] == b"Z"
    assert PADDING == b"\x00" * 4


