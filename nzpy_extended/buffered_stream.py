import asyncio
import socket as _socket
from ._constants import DEFAULT_BUFFER_SIZE
from .buffer_pool import global_pool


class NzBufferedStream:
    def __init__(self, sock: _socket.socket, max_size: int = DEFAULT_BUFFER_SIZE,
                 buffer_size: int | None = None) -> None:
        self.sock = sock
        self.max_size = max_size
        self._buffer_size = buffer_size if buffer_size is not None else max_size
        if self._buffer_size == global_pool.buffer_size:
            self.buffer: bytearray | None = global_pool.acquire()
            self._from_pool = True
        else:
            self.buffer = bytearray(self._buffer_size)
            self._from_pool = False
        self.view: memoryview | None = memoryview(self.buffer)
        self.head = 0
        self.tail = 0
        self.loop = asyncio.get_event_loop()

    def close(self) -> None:
        if self.buffer:
            if self._from_pool:
                global_pool.release(self.buffer)
            self.buffer = None
            self.view = None

    def read_view_sync(self, n: int) -> memoryview | None:
        view = self.view
        if view is None:
            return None
        avail = self.tail - self.head
        if avail >= n:
            result = view[self.head:self.head + n]
            self.head += n
            return result
        return None

    def read_available_view(self) -> memoryview | None:
        avail = self.tail - self.head
        if avail > 0:
            view = self.view
            if view is None:
                return None
            return view[self.head:self.tail]
        return None

    @property
    def buffered_available(self) -> int:
        return self.tail - self.head

    def has_buffered_non_null(self) -> bool:
        """True if the in-memory buffer contains any non-zero byte."""
        avail = self.tail - self.head
        if avail <= 0 or self.view is None:
            return False
        view = self.view[self.head:self.tail]
        for b in view:
            if b != 0:
                return True
        return False

    def discard_buffered_leading_nulls(self) -> int:
        """Advance head past leading 0x00 bytes already in the buffer. Returns count discarded."""
        n = 0
        while self.head < self.tail and self.view is not None and self.view[self.head] == 0:
            self.head += 1
            n += 1
        return n

    def advance_head(self, n: int) -> None:
        self.head += n

    async def read(self, n: int) -> bytes:
        if n == 0:
            return b""

        if n > self.max_size:
            if n > 100 * 1024 * 1024:
                raise ValueError(
                    f"Requested read size {n} exceeds maximum allowed 104857600"
                )
            result = bytearray(n)
            res_view = memoryview(result)
            bytes_read = 0

            avail = self.tail - self.head
            if avail > 0:
                view = self.view
                if view is None:
                    return bytes(result)
                to_copy = min(n, avail)
                res_view[:to_copy] = view[self.head:self.head + to_copy]
                self.head += to_copy
                bytes_read += to_copy

            while bytes_read < n:
                chunk_len = await self.loop.sock_recv_into(self.sock, res_view[bytes_read:])
                if not chunk_len:
                    break
                bytes_read += chunk_len
            return bytes(result)

        avail = self.tail - self.head
        if avail >= n:
            view = self.view
            if view is None:
                return b""
            result_bytes = view[self.head:self.head + n]
            self.head += n
            return bytes(result_bytes)

        result = bytearray(n)
        res_view = memoryview(result)
        bytes_read = 0

        while bytes_read < n:
            avail = self.tail - self.head
            if avail > 0:
                view = self.view
                if view is None:
                    return bytes(result)
                to_copy = min(n - bytes_read, avail)
                res_view[bytes_read:bytes_read+to_copy] = view[self.head:self.head+to_copy]
                self.head += to_copy
                bytes_read += to_copy

            if bytes_read < n:
                await self._fill_buffer()
                if self.tail == self.head:
                    break

        return bytes(result)

    async def _fill_buffer(self) -> None:
        self._rotate_buffer()
        view = self.view
        if view is None:
            return
        chunk_len = await self.loop.sock_recv_into(self.sock, view[self.tail:])
        self.tail += chunk_len

    def _rotate_buffer(self) -> None:
        avail = self.tail - self.head
        if avail > 0 and self.head > 0:
            view = self.view
            if view is None:
                return
            view[:avail] = view[self.head:self.tail]
        self.head = 0
        self.tail = avail

    async def write(self, data: bytes | bytearray) -> None:
        await self.loop.sock_sendall(self.sock, data)
