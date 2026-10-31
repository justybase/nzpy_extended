"""Phase 2 regressions: pool connects and sweeps never block other callers, and a
connection cannot be driven by two tasks at once. No live database required."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nzpy_extended import sync as sync_module
from nzpy_extended.core import Connection
from nzpy_extended.exceptions import InterfaceError
from nzpy_extended.pool import (NzPool, SyncPool, _PooledConnection,
                                _SyncPooledConnection)

pytestmark = pytest.mark.unit


def _reusable_conn() -> MagicMock:
    conn = MagicMock(name="conn")
    conn._is_reusable.return_value = True
    conn._active_cursor = None
    conn.autocommit = True
    conn.in_transaction = False
    conn.close = AsyncMock()
    return conn


@pytest.mark.asyncio
async def test_nzpool_connect_does_not_block_release_of_another_connection() -> None:
    pool = NzPool(min_size=0, max_size=2)
    held = _reusable_conn()
    pool._checked_out[id(held)] = held
    pool._created = 1
    gate = asyncio.Event()
    fresh = _reusable_conn()

    async def slow_create() -> Any:
        await gate.wait()
        return fresh

    pool._create_new_connection = slow_create  # type: ignore[method-assign]
    acquire = asyncio.create_task(pool.acquire())
    await asyncio.sleep(0.01)  # acquire is now mid-connect

    # Release used to queue behind the connect, because connect ran under the lock.
    await asyncio.wait_for(pool.release(held), timeout=1.0)
    gate.set()
    conn = await asyncio.wait_for(acquire, timeout=1.0)

    assert conn is fresh
    assert pool._created == 2


@pytest.mark.asyncio
async def test_nzpool_close_all_during_connect_closes_the_new_connection() -> None:
    pool = NzPool(min_size=0, max_size=1)
    fresh = _reusable_conn()
    gate = asyncio.Event()

    async def slow_create() -> Any:
        await gate.wait()
        return fresh

    pool._create_new_connection = slow_create  # type: ignore[method-assign]
    acquire = asyncio.create_task(pool.acquire())
    await asyncio.sleep(0.01)
    await pool.close_all()
    gate.set()

    with pytest.raises(RuntimeError, match="closed while connecting"):
        await acquire
    fresh.close.assert_awaited_once()
    assert pool._created == 0
    assert not pool._checked_out


@pytest.mark.asyncio
async def test_nzpool_sweep_owns_a_connection_while_validating_it() -> None:
    pool = NzPool(min_size=0, max_size=1, acquire_timeout=5)
    idle = _reusable_conn()
    now = time.monotonic()
    pool._pool.append(_PooledConnection(idle, now, now, 0))
    pool._created = 1
    validating = asyncio.Event()
    finish = asyncio.Event()

    async def slow_validate(pc: _PooledConnection) -> bool:
        validating.set()
        await finish.wait()
        return True

    pool._validate_connection = slow_validate  # type: ignore[method-assign]
    sweep = asyncio.create_task(pool._sweep_idle())
    await validating.wait()

    # The sweep holds the only connection, so acquire must wait rather than receive it.
    acquire = asyncio.create_task(pool.acquire())
    done, _ = await asyncio.wait({acquire}, timeout=0.05)
    assert not done

    finish.set()
    await asyncio.wait_for(sweep, timeout=1.0)
    conn = await asyncio.wait_for(acquire, timeout=1.0)

    assert conn is idle
    assert pool._created == 1
    assert not pool._reserving


@pytest.mark.asyncio
async def test_nzpool_cancelled_sweep_closes_the_connection_it_owns() -> None:
    pool = NzPool(min_size=0, max_size=1)
    idle = _reusable_conn()
    now = time.monotonic()
    pool._pool.append(_PooledConnection(idle, now, now, 0))
    pool._created = 1
    validating = asyncio.Event()

    async def hang(pc: _PooledConnection) -> bool:
        validating.set()
        await asyncio.Event().wait()
        return True

    pool._validate_connection = hang  # type: ignore[method-assign]
    sweep = asyncio.create_task(pool._sweep_idle())
    await validating.wait()
    sweep.cancel()

    with pytest.raises(asyncio.CancelledError):
        await sweep
    idle.close.assert_awaited_once()
    assert pool._created == 0
    assert not pool._reserving


@pytest.mark.asyncio
async def test_overlapping_execute_on_one_connection_fails_fast() -> None:
    conn = Connection()
    conn.autocommit = True
    gate = asyncio.Event()

    async def slow_execute(cursor: Any, operation: str, args: Any) -> None:
        await gate.wait()

    conn._execute = slow_execute  # type: ignore[method-assign,assignment]
    first = asyncio.create_task(conn.cursor().execute("select 1"))
    await asyncio.sleep(0.01)

    with pytest.raises(InterfaceError, match="Another operation is already in progress"):
        await conn.cursor().execute("select 2")

    gate.set()
    await asyncio.wait_for(first, timeout=1.0)
    assert conn._op_owner is None
    # Once the first operation has finished, the connection is usable again.
    await asyncio.wait_for(conn.cursor().execute("select 3"), timeout=1.0)


def test_syncpool_connect_runs_without_holding_the_pool_lock() -> None:
    pool = SyncPool(min_size=0, max_size=2, ping_query=None)
    held_during_connect: list[bool] = []

    def connect(**_kwargs: Any) -> MagicMock:
        held_during_connect.append(pool._lock.locked())
        return MagicMock(name="conn")

    try:
        with patch.object(sync_module, "connect", side_effect=connect):
            conn = pool.acquire()
        assert held_during_connect == [False]
        pool.release(conn)
    finally:
        pool.close_all()


def test_syncpool_connect_failure_does_not_leak_a_slot() -> None:
    pool = SyncPool(min_size=0, max_size=1, acquire_timeout=0.5, ping_query=None)
    good = MagicMock(name="conn")  # SyncPool closes synchronously, so no AsyncMock here
    attempts: list[int] = []

    def connect(**_kwargs: Any) -> MagicMock:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("boom")
        return good

    try:
        with patch.object(sync_module, "connect", side_effect=connect):
            with pytest.raises(RuntimeError, match="boom"):
                pool.acquire()
            # If the failed attempt kept its semaphore slot, this would time out.
            conn = pool.acquire()
        assert conn is good
        assert pool._created == 1
        pool.release(conn)
    finally:
        pool.close_all()


def test_syncpool_sweep_drops_a_stale_connection_and_keeps_the_count() -> None:
    pool = SyncPool(min_size=0, max_size=2, ping_query=None)
    stale = MagicMock(name="stale")
    now = time.monotonic()
    with pool._lock:
        pool._pool.append(_SyncPooledConnection(stale, now, now, 0))
        pool._created = 1
    try:
        with patch.object(SyncPool, "_validate_connection", return_value=False):
            pool._sweep_idle()
        stale.close.assert_called_once()
        assert pool._created == 0
        assert not pool._pool
    finally:
        pool.close_all()


def test_syncpool_acquire_waits_for_a_connection_the_sweep_is_validating() -> None:
    pool = SyncPool(min_size=0, max_size=1, acquire_timeout=5, ping_query=None)
    idle = MagicMock(name="idle")
    now = time.monotonic()
    with pool._lock:
        pool._pool.append(_SyncPooledConnection(idle, now, now, 0))
        pool._created = 1
    validating = threading.Event()
    finish = threading.Event()

    def slow_validate(_self: SyncPool, _pc: _SyncPooledConnection) -> bool:
        validating.set()
        finish.wait(5)
        return True

    got: list[Any] = []
    try:
        with patch.object(SyncPool, "_validate_connection", slow_validate):
            sweep = threading.Thread(target=pool._sweep_idle)
            sweep.start()
            assert validating.wait(5)
            waiter = threading.Thread(target=lambda: got.append(pool.acquire()))
            waiter.start()
            waiter.join(0.1)
            # The sweep still owns the only connection, so the waiter must not get it.
            assert waiter.is_alive()
            assert not got
            finish.set()
            sweep.join(5)
            waiter.join(5)
        assert got == [idle]
        assert pool._created == 1
        pool.release(idle)
    finally:
        finish.set()
        pool.close_all()
