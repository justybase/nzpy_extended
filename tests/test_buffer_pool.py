"""Unit tests for nzpy_extended.buffer_pool."""

from __future__ import annotations

import threading

import pytest

from nzpy_extended.buffer_pool import BufferPool, global_pool
from nzpy_extended._constants import DEFAULT_BUFFER_SIZE

pytestmark = pytest.mark.unit


def test_acquire_returns_sized_buffer() -> None:
    pool = BufferPool(buffer_size=1024)
    buf = pool.acquire()
    assert isinstance(buf, bytearray)
    assert len(buf) == 1024


def test_release_reuses_buffer() -> None:
    pool = BufferPool(buffer_size=512)
    buf = pool.acquire()
    buf[0] = 42
    pool.release(buf)
    reused = pool.acquire()
    assert reused is buf
    assert reused[0] == 42


def test_release_ignores_wrong_size() -> None:
    pool = BufferPool(buffer_size=256)
    wrong = bytearray(128)
    pool.release(wrong)
    assert len(pool._pool) == 0  # noqa: SLF001


def test_global_pool_thread_safe_reuse() -> None:
    errors: list[str] = []

    def worker() -> None:
        try:
            for _ in range(50):
                buf = global_pool.acquire()
                assert len(buf) == DEFAULT_BUFFER_SIZE
                global_pool.release(buf)
        except Exception as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_pool_caps_cached_buffers_and_reports_retained_bytes() -> None:
    pool = BufferPool(buffer_size=256, max_cached_buffers=2)
    buffers = [bytearray(256) for _ in range(3)]

    for buffer in buffers:
        pool.release(buffer)

    assert pool.cached_count == 2
    assert pool.cached_bytes == 512
    assert pool.acquire() is buffers[1]
    assert pool.acquire() is buffers[0]
    assert pool.cached_bytes == 0


def test_pool_rejects_invalid_capacity() -> None:
    with pytest.raises(ValueError, match="max_cached_buffers"):
        BufferPool(max_cached_buffers=-1)
