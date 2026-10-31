"""Recovery after protocol faults and interrupted reads (no live DB).

Covers: a read cancelled mid-frame retires the connection; a value error is
deferred to ReadyForQuery while the session stays in sync; a framing fault
closes the connection; and pools never reuse a connection that is closed,
desynced, or has stray bytes on an idle socket.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import nzpy_extended.sync as sync_nzpy
from nzpy_extended.buffered_stream import NzBufferedStream
from nzpy_extended.core import Connection
from nzpy_extended.exceptions import DataError, InterfaceError
from nzpy_extended.pool import NzPool, SyncPool, _PooledConnection, _SyncPooledConnection

pytestmark = pytest.mark.unit


def _msg(code: bytes, payload: bytes) -> bytes:
    """Message header (code plus four reserved bytes), length, then payload."""
    return code + b"\x00" * 4 + struct.pack(">i", len(payload)) + payload


def _ready() -> bytes:
    # ReadyForQuery carries no length: the header is the whole message.
    return b"Z" + b"\x00" * 4


def _text_row(value: bytes) -> bytes:
    # Bitmap is MSB-first (column 0 is bit 7 of byte 0), as in PostgreSQL DataRow.
    # Each cell is its length (including the four length bytes) plus the value.
    return _msg(b"D", b"\x80" + struct.pack(">i", 4 + len(value)) + value)


def _stub_stream() -> SimpleNamespace:
    return SimpleNamespace(partial_read=False, has_buffered_non_null=lambda: False)


def _make_conn(script: bytes) -> Connection:
    conn = Connection()
    conn.log = logging.getLogger("test.recovery")
    conn._stream = _stub_stream()  # type: ignore[assignment]
    scripted = bytearray(script)

    async def _read(n: int) -> bytes:
        if len(scripted) < n:
            raise AssertionError("read past the scripted protocol bytes")
        out = bytes(scripted[:n])
        del scripted[:n]
        return out

    conn._read = _read  # type: ignore[method-assign]
    conn._scripted = scripted  # type: ignore[attr-defined]
    conn.close = AsyncMock()  # type: ignore[method-assign]
    return conn


def _cursor_for(conn: Connection):
    cursor = conn.cursor()
    cursor.ps = {
        "row_desc": [{"name": b"c", "type_oid": 25, "type_modifier": -1, "format": 0}],
        "input_funcs": [lambda data, start, n: bytes(data[start:start + n]).decode("utf-8")],
    }
    return cursor


# ---------------------------------------------------------------------------
# Value errors are deferred; the stream stays in sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_value_fault_is_deferred_until_ready_for_query() -> None:
    script = _text_row(b"\xff\xfe") + _text_row(b"ok") + _ready()
    conn = _make_conn(script)
    cursor = _cursor_for(conn)

    states = [state async for state in conn._protocol._connNextResultSetGenerator(cursor)]

    assert states == ["DATA_ROW", "DATA_ROW", "READY_FOR_QUERY"]
    assert list(cursor.cached_rows) == [["ok"]]
    assert len(conn._scripted) == 0  # every message was consumed
    assert conn._frame_in_progress is False
    deferred = conn._take_deferred_decode_error()
    assert isinstance(deferred, DataError)


@pytest.mark.asyncio
async def test_fetch_raises_deferred_value_error_at_ready_for_query() -> None:
    conn = _make_conn(_text_row(b"\xff\xfe") + _text_row(b"ok") + _ready())
    cursor = _cursor_for(conn)
    cursor.generator = conn._protocol._connNextResultSetGenerator(cursor)

    assert await cursor._anext_internal() == ["ok"]
    with pytest.raises(DataError):
        await cursor._anext_internal()


def test_server_error_takes_precedence_over_deferred_value_error() -> None:
    conn = Connection()
    conn.error = InterfaceError("server said no")
    conn._defer_decode_error(DataError("value"))

    assert conn._take_deferred_decode_error() is None
    assert conn._deferred_decode_error is None


def test_only_the_first_deferred_value_error_is_kept() -> None:
    conn = Connection()
    first, second = DataError("first"), DataError("second")
    conn._defer_decode_error(first)
    conn._defer_decode_error(second)

    assert conn._take_deferred_decode_error() is first


# ---------------------------------------------------------------------------
# Framing faults close the connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_framing_fault_closes_connection() -> None:
    # RowDescription claims five fields but carries none of their descriptors.
    conn = _make_conn(_msg(b"T", struct.pack(">h", 5)))
    cursor = _cursor_for(conn)

    with pytest.raises(InterfaceError):
        _ = [state async for state in conn._protocol._connNextResultSetGenerator(cursor)]
    conn.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Interrupted reads mark the frame; retirement closes only then
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_inside_message_body_retires_connection() -> None:
    conn = Connection()
    conn.log = logging.getLogger("test.recovery")
    conn._stream = _stub_stream()  # type: ignore[assignment]
    conn.close = AsyncMock()  # type: ignore[method-assign]
    reads = deque([b"D" + b"\x00" * 4, struct.pack(">i", 10)])

    async def _read(n: int) -> bytes:
        if reads:
            return reads.popleft()
        raise asyncio.CancelledError()

    conn._read = _read  # type: ignore[method-assign]
    cursor = _cursor_for(conn)
    gen = conn._protocol._connNextResultSetGenerator(cursor)

    with pytest.raises(asyncio.CancelledError):
        await gen.__anext__()

    assert conn._frame_in_progress is True
    assert await conn._retire_if_desynced() is True
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_at_message_boundary_does_not_retire() -> None:
    conn = Connection()
    conn.close = AsyncMock()  # type: ignore[method-assign]
    conn._frame_in_progress = False

    assert await conn._retire_if_desynced() is False
    conn.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_read_is_flagged_when_cancelled_midway() -> None:
    a, b = socket.socketpair()
    try:
        a.setblocking(False)
        stream = NzBufferedStream(a, max_size=64, buffer_size=64)
        b.sendall(b"abc")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(stream.read(10), timeout=0.05)
        assert stream.partial_read is True
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Idle socket probe
# ---------------------------------------------------------------------------


def _connection_on(sock: socket.socket) -> Connection:
    conn = Connection()
    conn._usock = sock  # type: ignore[assignment]
    conn._stream = NzBufferedStream(sock, max_size=64, buffer_size=64)
    return conn


@pytest.mark.asyncio
async def test_idle_probe_accepts_quiet_socket() -> None:
    a, b = socket.socketpair()
    try:
        a.setblocking(False)
        assert _connection_on(a)._idle_socket_is_healthy() is True
    finally:
        a.close()
        b.close()


@pytest.mark.asyncio
async def test_idle_probe_rejects_stray_bytes() -> None:
    a, b = socket.socketpair()
    try:
        a.setblocking(False)
        b.sendall(b"Z")
        assert _connection_on(a)._idle_socket_is_healthy() is False
    finally:
        a.close()
        b.close()


@pytest.mark.asyncio
async def test_idle_probe_rejects_peer_eof() -> None:
    a, b = socket.socketpair()
    try:
        a.setblocking(False)
        b.close()
        assert _connection_on(a)._idle_socket_is_healthy() is False
    finally:
        a.close()


# ---------------------------------------------------------------------------
# Pools never reuse a connection that cannot be trusted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_pool_validation_rejects_dead_idle_socket() -> None:
    pool = NzPool(min_size=0, max_size=2, ping_query=None)
    conn = MagicMock()
    conn._idle_socket_is_healthy = MagicMock(return_value=False)
    now = time.monotonic()  # fresh, so only the socket probe can reject it
    pc = _PooledConnection(conn, now, now, 0)

    assert await pool._validate_connection(pc) is False
    conn.cursor.assert_not_called()


@pytest.mark.asyncio
async def test_async_pool_release_drops_unusable_connection() -> None:
    pool = NzPool(min_size=0, max_size=2, ping_query=None)
    conn = MagicMock()
    conn._is_reusable = MagicMock(return_value=False)
    conn.autocommit = True
    conn.in_transaction = False
    conn.close = AsyncMock()
    pool._created = 1
    pool._checked_out[id(conn)] = conn

    await pool.release(conn)

    conn.close.assert_awaited_once()
    assert len(pool._pool) == 0
    assert pool._created == 0


def test_sync_pool_validation_rejects_dead_idle_socket() -> None:
    pool = SyncPool(min_size=0, max_size=2, ping_query=None)
    async_conn = SimpleNamespace(_idle_socket_is_healthy=lambda: False)
    now = time.monotonic()  # fresh, so only the socket probe can reject it
    pc = _SyncPooledConnection(
        conn=SimpleNamespace(_conn=async_conn), created_at=now, last_used=now, use_count=0
    )

    assert pool._validate_connection(pc) is False


def test_sync_pool_release_drops_unusable_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    class DummyConn:
        _conn = SimpleNamespace(
            autocommit=True,
            in_transaction=False,
            _is_reusable=lambda: False,
        )

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(sync_nzpy, "connect", lambda **kwargs: DummyConn())
    pool = SyncPool(min_size=0, max_size=2, ping_query=None)
    conn = pool.acquire()
    created_before = pool._created

    pool.release(conn)

    assert closed == [True]
    assert len(pool._pool) == 0
    assert pool._created == created_before - 1
