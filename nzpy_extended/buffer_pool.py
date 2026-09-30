import threading

from ._constants import DEFAULT_BUFFER_SIZE


class BufferPool:
    def __init__(self, buffer_size: int = DEFAULT_BUFFER_SIZE, max_cached_buffers: int = 32) -> None:
        if buffer_size <= 0:
            raise ValueError("buffer_size must be positive")
        if max_cached_buffers < 0:
            raise ValueError("max_cached_buffers must not be negative")
        self.buffer_size = buffer_size
        self.max_cached_buffers = max_cached_buffers
        self._pool: list[bytearray] = []
        self._lock = threading.Lock()

    @property
    def cached_count(self) -> int:
        with self._lock:
            return len(self._pool)

    @property
    def cached_bytes(self) -> int:
        with self._lock:
            return len(self._pool) * self.buffer_size

    def acquire(self) -> bytearray:
        with self._lock:
            if self._pool:
                return self._pool.pop()
        return bytearray(self.buffer_size)

    def release(self, buffer: bytearray) -> None:
        if len(buffer) == self.buffer_size:
            with self._lock:
                if len(self._pool) < self.max_cached_buffers:
                    self._pool.append(buffer)


global_pool: BufferPool = BufferPool()


__all__ = ["BufferPool", "global_pool"]
