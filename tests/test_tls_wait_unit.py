"""TLS waits use the event loop's selector: no helper thread, and cancellation
leaves no reader or writer callback behind. No live database required."""

from __future__ import annotations

import asyncio
import socket

import pytest

from nzpy_extended.buffered_stream import NzBufferedStream
from nzpy_extended.exceptions import OperationalError

pytestmark = pytest.mark.unit


@pytest.fixture
def socket_pair() -> tuple[socket.socket, socket.socket]:
    a, b = socket.socketpair()
    a.setblocking(False)
    yield a, b
    a.close()
    b.close()


@pytest.mark.asyncio
async def test_tls_read_wait_resumes_when_the_socket_becomes_readable(
    socket_pair: tuple[socket.socket, socket.socket],
) -> None:
    a, b = socket_pair
    stream = NzBufferedStream(a, max_size=1024)
    waiter = asyncio.create_task(stream._wait_ssl(False))
    await asyncio.sleep(0.01)
    assert not waiter.done()

    b.send(b"x")
    await asyncio.wait_for(waiter, timeout=1.0)
    stream.close()


@pytest.mark.asyncio
async def test_tls_write_wait_returns_when_the_socket_is_writable(
    socket_pair: tuple[socket.socket, socket.socket],
) -> None:
    a, _ = socket_pair
    stream = NzBufferedStream(a, max_size=1024)
    await asyncio.wait_for(stream._wait_ssl(True), timeout=1.0)
    stream.close()


@pytest.mark.asyncio
async def test_cancelled_tls_wait_removes_its_callback(
    socket_pair: tuple[socket.socket, socket.socket],
) -> None:
    a, _ = socket_pair
    stream = NzBufferedStream(a, max_size=1024)
    loop = asyncio.get_running_loop()
    waiter = asyncio.create_task(stream._wait_ssl(False))
    await asyncio.sleep(0.01)
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter
    # remove_reader returns False when no callback is registered for the fd.
    assert not loop.remove_reader(a.fileno())
    stream.close()


@pytest.mark.asyncio
async def test_tls_wait_on_a_closed_socket_raises_a_driver_error(
    socket_pair: tuple[socket.socket, socket.socket],
) -> None:
    a, _ = socket_pair
    stream = NzBufferedStream(a, max_size=1024)
    a.close()
    with pytest.raises(OperationalError, match="Socket closed while waiting"):
        await stream._wait_ssl(False)
    stream.close()
