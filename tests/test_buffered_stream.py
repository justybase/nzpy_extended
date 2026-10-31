"""Unit tests for nzpy_extended.buffered_stream."""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import MagicMock

import pytest

from nzpy_extended.buffered_stream import NzBufferedStream
from nzpy_extended.exceptions import InterfaceError

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _event_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)


@pytest.fixture
def stream_sock() -> MagicMock:
    mock = MagicMock(spec=socket.socket)
    mock.fileno.return_value = 1
    return mock


def test_read_view_sync_from_buffer(stream_sock: MagicMock) -> None:
    stream = NzBufferedStream(stream_sock, max_size=64, buffer_size=64)
    data = b"hello world"
    stream.buffer[: len(data)] = data
    stream.tail = len(data)

    view = stream.read_view_sync(5)
    assert view is not None
    assert bytes(view) == b"hello"
    assert stream.head == 5


def test_advance_head_moves_cursor(stream_sock: MagicMock) -> None:
    stream = NzBufferedStream(stream_sock, max_size=32, buffer_size=32)
    stream.buffer[:4] = b"data"
    stream.tail = 4
    stream.advance_head(2)
    assert stream.head == 2


def test_read_available_view_returns_remaining(stream_sock: MagicMock) -> None:
    stream = NzBufferedStream(stream_sock, max_size=32, buffer_size=32)
    stream.buffer[:6] = b"abcdef"
    stream.tail = 6
    stream.head = 2
    view = stream.read_available_view()
    assert view is not None
    assert bytes(view) == b"cdef"


@pytest.mark.asyncio
async def test_read_rejects_oversized_request(stream_sock: MagicMock) -> None:
    stream = NzBufferedStream(stream_sock, max_size=1024, buffer_size=1024)
    with pytest.raises(InterfaceError, match="exceeds maximum allowed"):
        await stream.read(100 * 1024 * 1024 + 1)


@pytest.mark.asyncio
async def test_read_from_buffer_without_socket_io(stream_sock: MagicMock) -> None:
    stream = NzBufferedStream(stream_sock, max_size=64, buffer_size=64)
    stream.buffer[:3] = b"abc"
    stream.tail = 3
    result = await stream.read(3)
    assert result == b"abc"
    stream_sock.recv.assert_not_called()


@pytest.mark.asyncio
async def test_close_returns_buffer_to_pool(stream_sock: MagicMock) -> None:
    stream = NzBufferedStream(stream_sock, max_size=256, buffer_size=256)
    buf_id = id(stream.buffer)
    stream.close()
    assert stream.buffer is None
    stream2 = NzBufferedStream(stream_sock, max_size=256, buffer_size=256)
    if stream2._from_pool:
        assert id(stream2.buffer) == buf_id
