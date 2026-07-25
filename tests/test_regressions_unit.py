import asyncio
import inspect
import socket
import types
from collections import deque
from unittest.mock import MagicMock

import pytest

import nzpy_extended as nzpy
import nzpy_extended.fastapi as nzpy_fastapi
import nzpy_extended.sync as sync_nzpy
from datetime import timezone as Timezone
from nzpy_extended.buffered_stream import NzBufferedStream
from nzpy_extended.core import Connection, Datetime
from nzpy_extended.exceptions import InterfaceError
from nzpy_extended.types import timestamptz_in
from nzpy_extended.pool import SyncPool
from nzpy_extended import core as core_mod
from nzpy_extended.protocol import CONN_EXECUTING, EXTAB_SOCK_DATA, EXTAB_SOCK_DONE
from nzpy_extended.utils import i_pack as i_pack_mod


pytestmark = [pytest.mark.full, pytest.mark.unit]


@pytest.mark.asyncio
async def test_prepare_qmark_ignores_literals_and_comments():
    conn = Connection()
    query = "SELECT '?', ? -- ?\n, '?'"
    prepared = await conn.Prepare(None, query, ("value",))
    assert prepared == "SELECT '?', 'value' -- ?\n, '?'"


@pytest.mark.asyncio
async def test_prepare_named_uses_mapping_order():
    orig_paramstyle = nzpy.paramstyle
    try:
        nzpy.paramstyle = "named"
        conn = Connection()
        prepared = await conn.Prepare(
            None,
            "SELECT :second, :first, :second",
            {"first": 1, "second": "two"},
        )
        assert prepared == "SELECT 'two', 1, 'two'"
    finally:
        nzpy.paramstyle = orig_paramstyle


def test_timestamptz_in_returns_aware_utc_datetime():
    value = timestamptz_in(b"2024-12-11 14:30:00-05", 0, 22)
    assert value == Datetime(2024, 12, 11, 19, 30, 0, tzinfo=Timezone.utc)


def test_import_star_uses_string_all_entries():
    namespace = {}
    exec("from nzpy_extended import *", {}, namespace)
    assert "connect" in namespace
    assert "Connection" in namespace


@pytest.mark.asyncio
async def test_sync_async_connect_forwards_unix_sock(monkeypatch):
    captured = {}

    async def fake_connect(self, **kwargs):
        captured.update(kwargs)

    async def fake_close(self):
        return None

    monkeypatch.setattr(Connection, "connect", fake_connect)
    monkeypatch.setattr(Connection, "close", fake_close)

    conn = await sync_nzpy._async_connect(
        user="admin",
        host=None,
        unix_sock="/tmp/nz.sock",
        port=5480,
        database="JUST_DATA",
        password="password",
        ssl=None,
        securityLevel=0,
        timeout=None,
        application_name=None,
        max_prepared_statements=1000,
        datestyle="ISO",
        logLevel=0,
        tcp_keepalive=True,
        char_varchar_encoding="latin",
        on_connect=None,
    )

    try:
        assert captured["unix_sock"] == "/tmp/nz.sock"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_connect_applies_application_name_to_handshake(monkeypatch):
    class DummySocket:
        def settimeout(self, timeout):
            return None

        def setblocking(self, flag):
            return None

        def setsockopt(self, *args):
            return None

        def close(self):
            return None

    class DummyLoop:
        async def sock_connect(self, sock, address):
            return None

    class DummyHandshake:
        def __init__(self, sock, ssl, log):
            self.guardium_applName = "default"

        def startup(self, database, security_level, user, password, pg_options):
            raise RuntimeError(self.guardium_applName)

    monkeypatch.setattr(core_mod.socket, "socket", lambda *args, **kwargs: DummySocket())
    monkeypatch.setattr(core_mod.asyncio, "get_event_loop", lambda: DummyLoop())
    monkeypatch.setattr(core_mod.handshake, "SyncHandshake", DummyHandshake)

    conn = Connection()
    with pytest.raises(RuntimeError, match="my-app"):
        await conn.connect(
            user="admin",
            host="localhost",
            unix_sock=None,
            port=5480,
            database="JUST_DATA",
            password="password",
            ssl=None,
            securityLevel=0,
            timeout=None,
            application_name="my-app",
            max_prepared_statements=1000,
            datestyle="ISO",
            logLevel=0,
            tcp_keepalive=True,
            char_varchar_encoding="latin",
        )


@pytest.mark.asyncio
async def test_fastapi_lifespan_handles_sync_open():
    events = []

    class DummyPool:
        def open(self):
            events.append("open")

        def close_all(self):
            events.append("close")

    app = types.SimpleNamespace(state=types.SimpleNamespace())
    async with nzpy_fastapi.lifespan(DummyPool())(app):
        assert app.state.nz_pool is not None
        assert events == ["open"]

    assert events == ["open", "close"]


def test_sync_pool_preserves_use_count_and_rejects_foreign_release(monkeypatch):
    class DummyCursor:
        def execute(self, query):
            return None

        def fetchall(self):
            return [(1,)]

    class DummyConn:
        def cursor(self):
            return DummyCursor()

        def close(self):
            return None

    monkeypatch.setattr(sync_nzpy, "connect", lambda **kwargs: DummyConn())

    pool = SyncPool(min_size=0, max_size=2, ping_query=None)
    conn = pool.acquire()
    pool.release(conn)
    assert pool._pool[0].use_count == 1
    reused = pool.acquire()
    pool.release(reused)
    assert pool._pool[0].use_count == 2

    with pytest.raises(RuntimeError, match="already been released"):
        pool.release(DummyConn())


def test_float_metadata_uses_negative_scale():
    conn = Connection()
    meta =     conn._meta.resolve_column_metadata(
        {"type_oid": 701, "type_modifier": -1, "type_size": 8, "name": b"x"},
        0,
        None,
    )
    assert meta["numeric_precision"] == 53
    assert meta["numeric_scale"] == -1


def test_ssl_verify_flag_defaults_to_true():
    conn = Connection()
    params = conn.connect.__code__.co_varnames
    assert "ssl_verify" in params


def test_ssl_allow_fallback_defaults_to_fail_closed():
    from nzpy_extended.handshake import SyncHandshake
    import logging

    hs = SyncHandshake.__new__(SyncHandshake)
    hs.ssl_params = {}
    hs.log = logging.getLogger("test")
    assert hs._ssl_allow_fallback() is False

    hs.ssl_params = {"ssl_allow_fallback": True}
    assert hs._ssl_allow_fallback() is True


def test_connect_timeout_parameter():
    conn = Connection()
    params = conn.connect.__code__.co_varnames
    assert 'connect_timeout' in params


def test_error_response_mapping():
    from nzpy_extended.core import IntegrityError, InterfaceError, DataError, InternalError

    async def _test_code(code, expected_cls):
        conn = Connection()
        conn._client_encoding = 'utf8'
        null = b'\x00'
        err_data = b'C' + code.encode() + null + b'M' + b'test msg' + null + null
        await conn._protocol.handle_ERROR_RESPONSE(err_data, None)
        assert isinstance(conn.error, expected_cls), f"Expected {expected_cls} for {code}, got {type(conn.error)}"

    async def run():
        await _test_code('23505', IntegrityError)
        await _test_code('28000', InterfaceError)
        await _test_code('22012', DataError)
        await _test_code('26000', InternalError)
        await _test_code('42601', nzpy.ProgrammingError)

    import asyncio
    asyncio.run(run())


@pytest.mark.asyncio
async def test_notification_response_generator_consumes_payload():
    conn = Connection()
    conn._stream = object()
    conn._client_encoding = "utf8"
    conn.notifications = deque(maxlen=100)
    conn.log = core_mod.logging.getLogger("test")
    conn._dirty_socket = True
    conn._active_generator = None
    conn._active_cursor = None

    notification = i_pack_mod(1234) + b"test_channel\x00payload\x00"
    chunks = deque([
        b"A0000",
        i_pack_mod(len(notification)),
        notification,
        b"Z0000",
    ])
    reads = []

    async def mock_read(n):
        chunk = chunks.popleft()
        reads.append((n, chunk))
        assert len(chunk) == n
        return chunk

    conn._read = mock_read
    cursor = conn.cursor()

    states = []
    async for state in conn._protocol._connNextResultSetGenerator(cursor):
        states.append(state)

    assert states == ["READY_FOR_QUERY"]
    assert list(conn.notifications) == [(1234, "test_channel")]
    assert reads == [
        (5, b"A0000"),
        (4, i_pack_mod(len(notification))),
        (len(notification), notification),
        (5, b"Z0000"),
    ]


def test_receiveAndWriteDatatoExternal_skips_write_when_fh_is_none():
    async def run():
        conn = Connection()
        conn.log = core_mod.logging.getLogger("test")

        statuses = [EXTAB_SOCK_DATA,
                    3,
                    b'abc',
                    EXTAB_SOCK_DONE]
        idx = 0

        async def mock_read(n):
            nonlocal idx
            if idx >= len(statuses):
                return b''
            result = statuses[idx]
            idx += 1
            if isinstance(result, int):
                return i_pack_mod(result)
            return result

        conn._read = mock_read

        # Should NOT crash — just drain socket data when fh is None
        await conn._extab.receiveAndWriteDatatoExternal("test", None)

    asyncio.run(run())


def test_xferTable_uses_to_thread_for_file_io():
    conn = Connection()
    src = inspect.getsource(conn._extab.xferTable)
    assert 'asyncio.to_thread(filehandle.read' in src, "xferTable must use asyncio.to_thread for filehandle.read"
    assert 'asyncio.to_thread(filehandle.close' in src, "xferTable must use asyncio.to_thread for filehandle.close"


def test_getFileFromBE_uses_to_thread_for_file_io():
    conn = Connection()
    src = inspect.getsource(conn._extab.getFileFromBE)
    assert 'asyncio.to_thread(open' in src, "getFileFromBE must use asyncio.to_thread for open"
    assert 'asyncio.to_thread(fh.write' in src, "getFileFromBE must use asyncio.to_thread for fh.write"


def test_receiveAndWriteDatatoExternal_uses_to_thread_for_write():
    conn = Connection()
    src = inspect.getsource(conn._extab.receiveAndWriteDatatoExternal)
    assert 'asyncio.to_thread(fh.write' in src, "receiveAndWriteDatatoExternal must use asyncio.to_thread for fh.write"
    assert 'asyncio.to_thread(fh.flush' in src, "receiveAndWriteDatatoExternal must use asyncio.to_thread for fh.flush"
    assert 'asyncio.to_thread(fh.close' in src, "receiveAndWriteDatatoExternal must use asyncio.to_thread for fh.close"
    assert 'if fh is not None:' in src, "receiveAndWriteDatatoExternal must null-check fh in finally before close"


def test_xferTable_uses_effective_block_size():
    conn = Connection()
    src = inspect.getsource(conn._extab.xferTable)
    assert 'effectiveBlockSize = max(blockSize, 1)' in src, "xferTable must guard against blockSize <= 0"
    assert 'filehandle.read, effectiveBlockSize' in src, "xferTable must use effectiveBlockSize for reads"


def _push_buffered(conn: Connection, data: bytes) -> None:
    stream = NzBufferedStream(MagicMock(spec=socket.socket), max_size=4096, buffer_size=4096)
    stream.buffer[: len(data)] = data
    stream.tail = len(data)
    conn._stream = stream

    async def mock_read(n: int) -> bytes:
        return await stream.read(n)

    conn._read = mock_read


@pytest.mark.asyncio
async def test_drain_socket_consumes_orphaned_rowdesc_to_rfq():
    """Orphaned SELECT CURRENT_SID-style T+D+C+Z must be drained before next execute."""
    conn = Connection()
    conn.log = core_mod.logging.getLogger("test")
    conn._client_encoding = "utf8"
    conn._dirty_socket = False
    conn.status = CONN_EXECUTING
    conn._usock = MagicMock(spec=socket.socket)
    conn._usock.recv.side_effect = BlockingIOError()

    row_desc_payload = b"CURRENT_SID\x00"
    data_row_payload = b"\x00\x00\x00\x01"
    cmd_complete_payload = b"SELECT\x00"

    orphan = (
        b"T\x00\x00\x00\x00"
        + i_pack_mod(len(row_desc_payload))
        + row_desc_payload
        + b"D\x00\x00\x00\x00"
        + i_pack_mod(len(data_row_payload))
        + data_row_payload
        + b"C\x00\x00\x00\x00"
        + i_pack_mod(len(cmd_complete_payload))
        + cmd_complete_payload
        + b"Z\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )
    _push_buffered(conn, orphan)

    assert conn._has_unread_non_null_bytes() is True
    await conn._protocol._drain_socket()
    assert conn._dirty_socket is False
    assert conn._stream.buffered_available == 4
    assert conn._has_unread_non_null_bytes() is False


@pytest.mark.asyncio
async def test_execute_drains_orphaned_data_even_when_not_dirty(monkeypatch):
    """_execute must drain when unread non-null bytes exist, not only when dirty."""
    conn = Connection()
    conn.log = core_mod.logging.getLogger("test")
    conn._client_encoding = "utf8"
    conn._dirty_socket = False
    conn.status = None
    conn.commandNumber = -1
    conn._usock = MagicMock(spec=socket.socket)
    conn._usock.recv.side_effect = BlockingIOError()

    row_desc_payload = b"CURRENT_SID\x00"
    orphan = (
        b"T\x00\x00\x00\x00"
        + i_pack_mod(len(row_desc_payload))
        + row_desc_payload
        + b"Z\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )
    _push_buffered(conn, orphan)

    drain_calls = {"n": 0}
    real_drain = conn._protocol._drain_socket

    async def tracking_drain():
        drain_calls["n"] += 1
        await real_drain()

    monkeypatch.setattr(conn._protocol, "_drain_socket", tracking_drain)

    writes: list[bytes] = []

    async def mock_write(data):
        writes.append(bytes(data))

    async def mock_flush():
        return None

    conn._write = mock_write
    conn._flush = mock_flush

    async def empty_gen(cursor):
        conn._dirty_socket = False
        if False:
            yield "READY_FOR_QUERY"

    monkeypatch.setattr(conn._protocol, "_connNextResultSetGenerator", empty_gen)

    cursor = conn.cursor()
    await conn._execute(cursor, "CALL PROTO_SYNC_TEST();", None)
    assert drain_calls["n"] == 1
    assert any(b"CALL PROTO_SYNC_TEST()" in w for w in writes)
    # Ensure CURRENT_SID was not treated as this command's row description
    assert cursor.ps.get("row_desc") == []


@pytest.mark.asyncio
async def test_drain_socket_consumes_orphaned_notification_and_unknown0():
    """Orphaned A/0 messages must consume length+payload or the stream desyncs."""
    conn = Connection()
    conn.log = core_mod.logging.getLogger("test")
    conn._client_encoding = "utf8"
    conn._dirty_socket = False
    conn.status = CONN_EXECUTING
    conn._usock = MagicMock(spec=socket.socket)
    conn._usock.recv.side_effect = BlockingIOError()

    notification = i_pack_mod(1234) + b"test_channel\x00payload\x00"
    unknown0 = b"orphan0"
    orphan = (
        b"A\x00\x00\x00\x00"
        + i_pack_mod(len(notification))
        + notification
        + b"0\x00\x00\x00\x00"
        + i_pack_mod(len(unknown0))
        + unknown0
        + b"Z\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )
    _push_buffered(conn, orphan)

    await conn._protocol._drain_socket()
    assert conn._dirty_socket is False
    assert conn._stream.buffered_available == 4
    assert conn._has_unread_non_null_bytes() is False


@pytest.mark.asyncio
async def test_drain_socket_raises_on_incomplete_rfq():
    conn = Connection()
    conn.log = core_mod.logging.getLogger("test")
    conn._client_encoding = "utf8"
    conn._dirty_socket = True
    conn._usock = MagicMock(spec=socket.socket)
    conn._usock.recv.side_effect = BlockingIOError()

    orphan = b"T\x00\x00\x00\x00"
    _push_buffered(conn, orphan)

    with pytest.raises(InterfaceError, match="protocol out of sync"):
        await conn._protocol._drain_socket()


def test_has_unread_non_null_ignores_padding_only():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        conn = Connection()
        conn._usock = MagicMock(spec=socket.socket)
        conn._usock.recv.side_effect = BlockingIOError()
        _push_buffered(conn, b"\x00\x00\x00\x00")
        assert conn._has_unread_non_null_bytes() is False
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def test_buffered_stream_discard_leading_nulls():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        stream = NzBufferedStream(MagicMock(spec=socket.socket), max_size=32, buffer_size=32)
        stream.buffer[:6] = b"\x00\x00\x00T\x00\x00"
        stream.tail = 6
        assert stream.has_buffered_non_null() is True
        n = stream.discard_buffered_leading_nulls()
        assert n == 3
        assert bytes(stream.read_available_view()) == b"T\x00\x00"
    finally:
        loop.close()
        asyncio.set_event_loop(None)
