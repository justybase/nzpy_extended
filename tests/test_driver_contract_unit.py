"""Failure-path regressions independent of a Netezza server."""
import logging
import asyncio
import shutil
import subprocess
import socket
import ssl
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import nzpy_extended as nz
from nzpy_extended import sync
from nzpy_extended.buffered_stream import NzBufferedStream
from nzpy_extended.core import Connection
from nzpy_extended.handshake import SyncHandshake
from _helpers import compare_rows

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [5, 32])
async def test_short_read_fails_without_fabricating_bytes(size):
    stream = NzBufferedStream(MagicMock(), max_size=8, buffer_size=8)
    stream.buffer[:2] = b'ab'
    stream.tail = 2
    stream.loop = MagicMock()
    stream.loop.sock_recv_into = AsyncMock(return_value=0)
    with pytest.raises(nz.OperationalError, match="EOF"):
        await stream.read(size)
    with pytest.raises(nz.InterfaceError, match="closed"):
        await stream.read(1)


@pytest.mark.asyncio
async def test_fragmented_read_is_exact():
    stream = NzBufferedStream(MagicMock(), max_size=8, buffer_size=8)
    chunks = iter([b'a', b'bc', b'def'])
    async def recv(sock, view):
        data = next(chunks)
        view[:len(data)] = data
        return len(data)
    stream.loop = MagicMock()
    stream.loop.sock_recv_into = recv
    assert await stream.read(5) == b'abcde'
    assert await stream.read(1) == b'f'
    stream.close()


def connection_stub():
    c = Connection()
    c.log = logging.getLogger('test')
    c.commit = AsyncMock()
    c.rollback = AsyncMock()
    c.close = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_commit_failure_propagates_and_closes():
    c = connection_stub()
    c.commit.side_effect = nz.OperationalError('commit failed')
    with pytest.raises(nz.OperationalError, match='commit failed'):
        async with c:
            pass
    c.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_failure_preserves_application_exception():
    c = connection_stub()
    c.rollback.side_effect = nz.OperationalError('rollback failed')
    with pytest.raises(ValueError, match='application'):
        async with c:
            raise ValueError('application')
    c.close.assert_awaited_once()


def test_sync_commit_failure_propagates_and_closes():
    c = connection_stub()
    c.commit.side_effect = nz.OperationalError('commit failed')
    with pytest.raises(nz.OperationalError, match='commit failed'):
        with sync.SyncConnection(c):
            pass
    c.close.assert_awaited_once()


@pytest.mark.parametrize('method', ['commit', 'rollback', 'cursor'])
def test_closed_connection_raises_dbapi_error(method):
    c = sync.SyncConnection(None)
    with pytest.raises(nz.Error):
        getattr(c, method)()


@pytest.mark.parametrize('method', ['fetchone', 'fetchmany', 'fetchall', 'nextset'])
def test_closed_cursor_raises_dbapi_error(method):
    c = sync.SyncCursor(None)
    with pytest.raises(nz.InterfaceError):
        getattr(c, method)()


@pytest.mark.parametrize('oid', [20, 21, 23, 700, 701, 1700, 2500])
def test_numeric_category_matches_all_oids(oid):
    assert oid == sync.NUMBER
    assert sync.NUMBER == oid
    assert not (sync.NUMBER != oid)
    assert oid != sync.STRING


@pytest.mark.parametrize('oid', [19, 25, 1042, 1043, 2522, 2530])
def test_string_category_matches_all_oids(oid):
    assert oid == sync.STRING
    assert sync.STRING == oid
    assert not (sync.STRING != oid)
    assert oid != sync.NUMBER


def test_module_contract():
    for name in ('apilevel', 'threadsafety', 'paramstyle', 'Error', 'DataError',
                 'Date', 'Time', 'Timestamp', 'Binary', 'STRING', 'NUMBER', 'BINARY'):
        assert getattr(sync, name) == getattr(nz, name)
    assert sync.Binary(bytearray(b'abc')) == b'abc'
    assert isinstance(b'abc', nz.BINARY)


@pytest.mark.asyncio
async def test_closed_connection_invalidates_cached_cursor_rows():
    conn = Connection()
    cur = conn.cursor()
    cur.cached_rows.append([42])
    await conn.close()
    with pytest.raises(nz.InterfaceError):
        await cur.fetchone()


@pytest.mark.asyncio
async def test_protocol_eof_closes_owning_connection():
    conn = Connection()
    sock = MagicMock(spec=socket.socket)
    conn._usock = sock
    conn._stream = NzBufferedStream(sock, max_size=8, buffer_size=8, on_fatal=conn.close)
    conn._stream.loop = MagicMock()
    conn._stream.loop.sock_recv_into = AsyncMock(return_value=0)

    with pytest.raises(nz.OperationalError, match='EOF'):
        await conn._stream.read(1)

    assert conn._closed is True
    assert conn._usock is None
    sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    sock.close.assert_called_once()


@pytest.mark.asyncio
async def test_failed_async_connect_callback_closes_connection(monkeypatch):
    conn = connection_stub()
    conn.connect = AsyncMock()
    monkeypatch.setattr(nz, 'Connection', lambda: conn)
    def callback(c):
        raise ValueError('callback failed')
    with pytest.raises(ValueError, match='callback failed'):
        await nz.connect(user='test', on_connect=callback)
    conn.close.assert_awaited_once()


def test_required_tls_cannot_downgrade_even_with_fallback_option():
    hs = SyncHandshake(MagicMock(spec=socket.socket), {'ssl_allow_fallback': True}, logging.getLogger('test'))
    hs._read = MagicMock(return_value=b'N')
    assert hs.conn_secure_session(3) is False


@pytest.mark.parametrize('left,right', [
    ('abcdef', 'abc'), (' x ', 'x'), (1000000, 1000500),
    (Decimal('1.000000000000000001'), Decimal('1.000000000000000002')),
    ('2024-01-01 00:00:00', '2024-01-01 00:00:04'),
])
def test_parity_rejects_data_loss(left, right):
    with pytest.raises(AssertionError):
        compare_rows([[left]], [[right]], 'regression')


def test_handshake_passes_hostname_and_uses_file_stream(monkeypatch):
    raw = MagicMock(spec=socket.socket)
    hs = SyncHandshake(raw, {'server_hostname': 'db.example'}, logging.getLogger('test'))
    hs._read = MagicMock(side_effect=[b'S', b'N'])
    context = MagicMock()
    monkeypatch.setattr(ssl, 'create_default_context', lambda **kw: context)
    assert hs.conn_secure_session(3)
    context.wrap_socket.assert_called_once_with(raw, server_hostname='db.example')
    assert hs._sock is context.wrap_socket.return_value.makefile.return_value


@pytest.mark.asyncio
async def test_tls_transport_retries_want_read_and_partial_writes():
    sock = MagicMock(spec=ssl.SSLSocket)
    stream = NzBufferedStream(sock, max_size=8, buffer_size=8)
    stream._wait_ssl = AsyncMock()
    sock.recv_into.side_effect = [ssl.SSLWantReadError(), 2]
    assert await stream._recv_into(memoryview(bytearray(2))) == 2
    stream._wait_ssl.assert_awaited_once_with(False)
    sock.send.side_effect = [ssl.SSLWantWriteError(), 1, 2]
    await stream.write(b'abc')
    assert sock.send.call_count == 3
    stream.close()


@pytest.mark.asyncio
async def test_tls_peek_preserves_pending_bytes_and_null_padding():
    sock = MagicMock(spec=ssl.SSLSocket)
    stream = NzBufferedStream(sock, max_size=8, buffer_size=8)
    sock.pending.return_value = 2
    def receive(view, size):
        view[:2] = b'\x00\x00'
        return 2
    sock.recv_into.side_effect = receive
    assert stream.peek_tls() == b'\x00\x00'
    assert not stream.has_buffered_non_null()
    assert await stream.read(2) == b'\x00\x00'
    stream.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('hostname', ['localhost', 'wrong.example'])
async def test_real_tls_roundtrip_and_hostname_verification(tmp_path, hostname):
    openssl = shutil.which('openssl')
    if not openssl:
        pytest.skip('openssl executable required for local TLS certificate')
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1',
                    '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
                    '-addext', 'basicConstraints=critical,CA:TRUE'],
                   check=True, capture_output=True)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert, key)
    done = asyncio.Event()
    async def echo(reader, writer):
        try:
            data = await reader.readexactly(5)
            writer.write(data)
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass
            done.set()
    server = await asyncio.start_server(echo, '127.0.0.1', 0, ssl=server_ctx)
    raw = socket.socket()
    raw.setblocking(False)
    stream = None
    try:
        await asyncio.get_running_loop().sock_connect(raw, server.sockets[0].getsockname())
        client_ctx = ssl.create_default_context(cafile=str(cert))
        wrapped = client_ctx.wrap_socket(raw, server_hostname=hostname, do_handshake_on_connect=False)
        stream = NzBufferedStream(wrapped, max_size=8, buffer_size=8)
        async def handshake():
            while True:
                try:
                    wrapped.do_handshake()
                    return
                except ssl.SSLWantReadError:
                    await stream._wait_ssl(False)
                except ssl.SSLWantWriteError:
                    await stream._wait_ssl(True)
        if hostname != 'localhost':
            with pytest.raises(ssl.SSLCertVerificationError):
                await asyncio.wait_for(handshake(), 5)
        else:
            await asyncio.wait_for(handshake(), 5)
            await stream.write(b'hello')
            assert await asyncio.wait_for(stream.read(5), 5) == b'hello'
    finally:
        if stream is not None:
            stream.sock.close()
            stream.close()
        raw.close()
        server.close()
        await server.wait_closed()
        if hostname == 'localhost':
            await asyncio.wait_for(done.wait(), 5)
