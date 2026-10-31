from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any

from nzpy_extended.core import Connection

from . import sync as _sync


def _clear_stale_cursor(conn: Connection) -> None:
    """Drop buffered rows left on a connection after partial read / cancel."""
    stale = getattr(conn, "_active_cursor", None)
    if stale is not None:
        stale.cached_rows.clear()
        stale.generator = None
        conn._active_cursor = None


_CONNECT_DEFAULTS: dict[str, Any] = {
    'unix_sock': None,
    'ssl': None,
    'securityLevel': 0,
    'timeout': None,
    'application_name': None,
    'max_prepared_statements': 1000,
    'datestyle': 'ISO',
    'logLevel': 0,
    'tcp_keepalive': True,
    'char_varchar_encoding': 'latin',
    'client_encoding': 'utf8',
}


@dataclass
class _PooledConnection:
    conn: Connection
    created_at: float
    last_used: float
    use_count: int


@dataclass
class _SyncPooledConnection:
    conn: Any
    created_at: float
    last_used: float
    use_count: int


class NzPool:
    def __init__(
        self,
        min_size: int = 1,
        max_size: int = 10,
        idle_timeout: float = 300.0,
        max_lifetime: float = 3600.0,
        max_uses: int = 1000,
        acquire_timeout: float = 30.0,
        ping_query: str | None = "SELECT 1",
        on_connect: Callable[[Connection], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.min_size = min_size
        self.max_size = max_size
        if self.min_size > self.max_size:
            raise ValueError(
                f"min_size ({min_size}) cannot exceed max_size ({max_size})"
        )
        self.idle_timeout = idle_timeout
        self.max_lifetime = max_lifetime
        self.max_uses = max_uses
        self.acquire_timeout = acquire_timeout
        self.ping_query = ping_query
        self.on_connect = on_connect
        self.kwargs = kwargs

        self._pool: deque[_PooledConnection] = deque()
        self._created = 0
        self._cond = asyncio.Condition()
        self._closed = False
        self._checked_out: dict[int, Connection] = {}
        self._log = logging.getLogger("nzpy_extended.NzPool")
        self._maintain_task: asyncio.Task[None] | None = None
        self._reserving: set[int] = set()

    async def _create_new_connection(self) -> Connection:
        conn = Connection()
        merged = dict(_CONNECT_DEFAULTS)
        merged.update(self.kwargs)
        await conn.connect(**merged)
        conn._nzpy_pool_created = time.monotonic()  # type: ignore[attr-defined]
        conn._nzpy_pool_uses = 0  # type: ignore[attr-defined]
        if self.on_connect is not None:
            try:
                result = self.on_connect(conn)
                if hasattr(result, '__await__'):
                    await result
            except Exception:
                try:
                    await conn.close()
                except Exception as exc:
                    self._log.debug(
                        "Error closing connection after on_connect failure: %s",
                        exc,
                        exc_info=True,
                    )
                raise
        return conn

    async def _validate_connection(self, pc: _PooledConnection) -> bool:
        now = time.monotonic()
        if self.idle_timeout > 0 and (now - pc.last_used) > self.idle_timeout:
            return False
        if self.max_lifetime > 0 and (now - pc.created_at) > self.max_lifetime:
            return False
        if self.max_uses > 0 and pc.use_count >= self.max_uses:
            return False
        if not pc.conn._idle_socket_is_healthy():
            return False
        if self.ping_query:
            try:
                cursor = pc.conn.cursor()
                await asyncio.wait_for(
                    pc.conn._execute(cursor, self.ping_query, None),
                    timeout=10.0
                )
                await cursor.fetchall()
            except (asyncio.TimeoutError, Exception):
                return False
        return True

    async def _close_connection(self, pc: _PooledConnection) -> None:
        try:
            await pc.conn.close()
        except Exception as exc:
            self._log.debug(
                "Error closing pooled connection: %s", exc, exc_info=True
            )

    # Pool bookkeeping (``_created``, ``_pool``, ``_checked_out``, ``_reserving``) is
    # updated with plain attribute writes and no ``await`` between a check and its
    # update. The event loop is single-threaded, so these stay consistent without
    # holding ``_cond``. ``_cond`` only guards waiting and notifying, and it is never
    # held across network I/O: connects, closes, and validation pings all run outside it.

    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify(1)

    async def _close_quiet(self, conn: Connection) -> None:
        try:
            await conn.close()
        except Exception as exc:
            self._log.debug(
                "Error closing pooled connection: %s", exc, exc_info=True
            )

    async def _abandon_slot(self) -> None:
        """Give back a slot reserved for a connection that never opened."""
        self._created = max(0, self._created - 1)
        await self._notify()

    async def _drop_owned(self, pc: _PooledConnection) -> None:
        """Discard an idle connection that the calling task has reserved."""
        self._reserving.discard(id(pc.conn))
        self._created = max(0, self._created - 1)
        try:
            await self._notify()
        finally:
            await self._close_connection(pc)

    async def _connect_reserved(self) -> Connection:
        """Open a connection for a slot that is already counted in ``_created``."""
        try:
            conn = await self._create_new_connection()
        except BaseException:
            await self._abandon_slot()
            raise
        if self._closed:
            self._created = max(0, self._created - 1)
            await self._close_quiet(conn)
            await self._notify()
            raise RuntimeError("Pool was closed while connecting")
        conn._nzpy_pool_uses = 1  # type: ignore[attr-defined]
        self._checked_out[id(conn)] = conn
        return conn

    async def _fill_idle(self) -> None:
        """Pre-create idle connections up to ``min_size``; never connects under ``_cond``."""
        while not self._closed and self._created < self.min_size:
            self._created += 1
            try:
                conn = await self._create_new_connection()
            except Exception as e:
                self._log.warning("Failed to pre-create connection: %s", e)
                await self._abandon_slot()
                return
            except BaseException:
                await self._abandon_slot()
                raise
            if self._closed:
                self._created = max(0, self._created - 1)
                await self._close_quiet(conn)
                return
            now = time.monotonic()
            self._pool.append(_PooledConnection(conn, now, now, 0))
            await self._notify()

    def get_stats(self) -> dict[str, Any]:
        """Return a best-effort snapshot of pool counters."""
        return {
            "type":           "NzPool",
            "pool_min":       self.min_size,
            "pool_max":       self.max_size,
            "pool_size":      self._created,
            "pool_available": len(self._pool),
            "pool_in_use":    len(self._checked_out) + len(self._reserving),
            "pool_closed":    self._closed,
        }

    async def open(self) -> None:
        await self._fill_idle()
        if self._maintain_task is None and not self._closed:
            self._maintain_task = asyncio.create_task(self._background_maintain())

    async def _sweep_idle(self) -> None:
        """Validate idle connections one at a time, owning each while it is checked.

        A connection leaves the idle queue before it is validated, so acquire can
        never receive a connection the sweep is still testing. It goes back to the
        queue if healthy, and is closed otherwise, including when the sweep is
        cancelled mid-validation.
        """
        async with self._cond:
            budget = len(self._pool)
        for _ in range(budget):
            async with self._cond:
                if self._closed or not self._pool:
                    return
                pc = self._pool.popleft()
                self._reserving.add(id(pc.conn))
            returned = False
            try:
                if await self._validate_connection(pc) and not self._closed:
                    self._reserving.discard(id(pc.conn))
                    self._pool.append(pc)
                    returned = True
                    await self._notify()
            finally:
                if not returned:
                    await self._drop_owned(pc)

    async def _background_maintain(self) -> None:
        while not self._closed:
            await asyncio.sleep(30)
            if self._closed:
                return
            await self._sweep_idle()
            await self._fill_idle()

    async def _checkout_idle(self, pc: _PooledConnection) -> Connection | None:
        """Validate an idle connection reserved by this task. ``None`` if it was stale."""
        checked_out = False
        try:
            if not await self._validate_connection(pc):
                return None
            if self._closed:
                raise RuntimeError("Pool was closed during connection validation")
            pc.use_count += 1
            self._reserving.discard(id(pc.conn))
            pc.conn._nzpy_pool_uses = pc.use_count  # type: ignore[attr-defined]
            self._checked_out[id(pc.conn)] = pc.conn
            checked_out = True
            return pc.conn
        finally:
            if not checked_out:
                await self._drop_owned(pc)

    async def acquire(self) -> Connection:
        if self._closed:
            raise RuntimeError("Pool is closed")

        deadline = time.monotonic() + self.acquire_timeout if self.acquire_timeout > 0 else float("inf")

        while True:
            if self._closed:
                raise RuntimeError("Pool is closed")
            await self._fill_idle()

            pc: _PooledConnection | None = None
            reserved = False
            async with self._cond:
                if self._pool:
                    pc = self._pool.popleft()
                    self._reserving.add(id(pc.conn))
                elif self._created < self.max_size:
                    self._created += 1
                    reserved = True
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"Timed out waiting for a connection from pool "
                            f"(created={self._created}, max_size={self.max_size})"
                        )
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        raise TimeoutError(
                            f"Timed out waiting for a connection from pool "
                            f"(created={self._created}, max_size={self.max_size})"
                        )
                    continue

            if reserved:
                return await self._connect_reserved()
            if pc is not None:
                conn = await self._checkout_idle(pc)
                if conn is not None:
                    return conn

    async def release(self, conn: Connection) -> None:
        conn_id = id(conn)

        if conn._is_reusable() and not conn.autocommit and conn.in_transaction:
            try:
                await conn.rollback()
            except Exception as exc:
                self._log.debug(
                    "Error rolling back connection on release: %s",
                    exc,
                    exc_info=True,
                )

        _clear_stale_cursor(conn)

        closed = False
        discard = False
        async with self._cond:
            if self._closed:
                closed = True
            else:
                if conn_id not in self._checked_out:
                    raise RuntimeError(
                        "Connection was not acquired from this pool or has already been released."
                    )
                self._checked_out.pop(conn_id)

                if not conn._is_reusable():
                    # Never hand a closed or desynced connection to the next caller.
                    discard = True
                    self._created = max(0, self._created - 1)
                else:
                    now = time.monotonic()
                    created = getattr(conn, '_nzpy_pool_created', now)
                    uses = getattr(conn, '_nzpy_pool_uses', 0)
                    self._pool.append(_PooledConnection(conn, created, now, uses))
                self._cond.notify(1)

        # Closing is network I/O, so it happens after the lock is released.
        if closed:
            await self._close_quiet(conn)
        elif discard:
            self._log.debug("Dropping unusable connection on release")
            await self._close_quiet(conn)

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[Connection, None]:
        conn = await self.acquire()
        try:
            yield conn
        finally:
            await self.release(conn)

    async def close_all(self) -> None:
        if self._maintain_task is not None:
            self._maintain_task.cancel()
            self._maintain_task = None
        async with self._cond:
            self._closed = True
            idle = list(self._pool)
            self._pool.clear()
            checked_out = list(self._checked_out.values())
            self._checked_out.clear()
            self._created = 0
            self._cond.notify_all()
        for pc in idle:
            await self._close_connection(pc)
        for conn in checked_out:
            await self._close_quiet(conn)

    async def __aenter__(self) -> NzPool:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close_all()


class SyncPool:
    def __init__(
        self,
        min_size: int = 1,
        max_size: int = 10,
        idle_timeout: float = 300.0,
        max_lifetime: float = 3600.0,
        max_uses: int = 1000,
        acquire_timeout: float = 30.0,
        ping_query: str | None = "SELECT 1",
        on_connect: Callable[[Any], Any] | None = None,
        **conn_kwargs: Any,
    ) -> None:
        if min_size > max_size:
            raise ValueError(
                f"min_size ({min_size}) cannot exceed max_size ({max_size})"
            )
        self.min_size = min_size
        self.max_size = max_size
        self.idle_timeout = idle_timeout
        self.max_lifetime = max_lifetime
        self.max_uses = max_uses
        self.acquire_timeout = acquire_timeout
        self.ping_query = ping_query
        self.on_connect = on_connect
        self._kwargs = conn_kwargs

        # Lock rules: _lock guards the bookkeeping only. It is never held across a
        # connect, close, rollback, or validation ping. _sem bounds connections in use;
        # _created counts every open connection, including idle and sweep-owned ones.
        self._pool: deque[_SyncPooledConnection] = deque()
        self._lock = threading.Lock()
        self._sem = threading.Semaphore(max_size)
        self._created = 0
        self._checked_out: set[int] = set()
        self._checked_out_pc: dict[int, _SyncPooledConnection] = {}
        self._closed = False
        self._stop_event = threading.Event()
        self._log = logging.getLogger("nzpy_extended.SyncPool")
        self._maintain_thread = threading.Thread(
            target=self._maintain_loop, daemon=True
        )
        self._fill_idle()
        self._maintain_thread.start()

    def _close_quiet(self, conn: Any) -> None:
        try:
            conn.close()
        except Exception as exc:
            self._log.debug(
                "Error closing pooled connection: %s", exc, exc_info=True
            )

    def _drop_owned(self, pc: _SyncPooledConnection) -> None:
        """Discard a connection the calling thread owns (its slot is still counted)."""
        with self._lock:
            self._created = max(0, self._created - 1)
        self._close_quiet(pc.conn)

    def _fill_idle(self) -> None:
        """Open idle connections up to ``min_size``; connects run without ``_lock``."""
        while True:
            with self._lock:
                if self._closed or self._created >= self.min_size:
                    return
                self._created += 1
            try:
                conn = _sync.connect(on_connect=self.on_connect, **self._kwargs)
            except Exception as exc:
                with self._lock:
                    self._created = max(0, self._created - 1)
                self._log.debug("Failed to pre-create connection: %s", exc, exc_info=True)
                return
            except BaseException:
                with self._lock:
                    self._created = max(0, self._created - 1)
                raise
            now = time.monotonic()
            with self._lock:
                closed = self._closed
                if closed:
                    self._created = max(0, self._created - 1)
                else:
                    self._pool.append(_SyncPooledConnection(conn, now, now, 0))
            if closed:
                self._close_quiet(conn)
                return

    def open(self) -> None:
        self._fill_idle()

    def _validate_connection(self, pc: _SyncPooledConnection) -> bool:
        now = time.monotonic()
        if self.idle_timeout > 0 and (now - pc.last_used) > self.idle_timeout:
            return False
        if self.max_lifetime > 0 and (now - pc.created_at) > self.max_lifetime:
            return False
        if self.max_uses > 0 and pc.use_count >= self.max_uses:
            return False
        async_conn = getattr(pc.conn, "_conn", None)
        if async_conn is not None and not async_conn._idle_socket_is_healthy():
            return False
        if self.ping_query:
            try:
                cur = pc.conn.cursor()
                cur.execute(self.ping_query)
                cur.fetchall()
            except Exception:
                return False
        return True

    def _sweep_idle(self) -> None:
        """Validate idle connections one at a time, owning each while it is checked.

        A connection leaves the idle queue before validation, so acquire never gets a
        connection the sweep is still testing. Healthy ones return to the queue;
        the rest are closed.
        """
        with self._lock:
            budget = len(self._pool)
        for _ in range(budget):
            with self._lock:
                if self._closed or not self._pool:
                    return
                pc = self._pool.popleft()
            returned = False
            try:
                if self._validate_connection(pc):
                    with self._lock:
                        if not self._closed:
                            self._pool.append(pc)
                            returned = True
            finally:
                if not returned:
                    self._drop_owned(pc)

    def _maintain_loop(self) -> None:
        while not self._stop_event.wait(30.0):
            try:
                self._sweep_idle()
                self._fill_idle()
            except Exception:
                self._log.debug("Pool maintenance pass failed", exc_info=True)

    def acquire(self) -> Any:
        if self._closed:
            raise RuntimeError("Pool is closed")
        deadline = time.monotonic() + self.acquire_timeout
        acquired = self._sem.acquire(timeout=self.acquire_timeout)
        if not acquired:
            raise TimeoutError(
                f"Could not acquire connection within {self.acquire_timeout}s "
                f"(pool_size={self._created}, in_use={len(self._checked_out)})"
            )
        try:
            return self._checkout(deadline)
        except BaseException:
            # The caller never got the connection, so its slot goes back.
            self._sem.release()
            raise

    def _checkout(self, deadline: float) -> Any:
        """Return an idle or new connection. The caller already holds a ``_sem`` slot."""
        while True:
            pc: _SyncPooledConnection | None = None
            create = False
            with self._lock:
                if self._closed:
                    raise RuntimeError("Pool was closed while waiting for a connection")
                if self._pool:
                    pc = self._pool.popleft()
                elif self._created < self.max_size:
                    self._created += 1
                    create = True
            if create:
                return self._connect_reserved()
            if pc is not None:
                conn = self._checkout_idle(pc)
                if conn is not None:
                    return conn
                continue
            # Every slot is counted but none is idle, so the maintenance thread is
            # validating one. It returns the connection shortly, or the deadline ends the wait.
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Could not acquire connection within {self.acquire_timeout}s "
                    f"(pool_size={self._created}, in_use={len(self._checked_out)})"
                )
            time.sleep(0.005)

    def _checkout_idle(self, pc: _SyncPooledConnection) -> Any | None:
        """Validate an idle connection owned by this thread. ``None`` if it was stale."""
        checked_out = False
        try:
            if not self._validate_connection(pc):
                return None
            with self._lock:
                if self._closed:
                    raise RuntimeError(
                        "Pool was closed during connection validation"
                    )
                pc.use_count += 1
                self._checked_out.add(id(pc.conn))
                self._checked_out_pc[id(pc.conn)] = pc
                checked_out = True
            return pc.conn
        finally:
            if not checked_out:
                self._drop_owned(pc)

    def _connect_reserved(self) -> Any:
        """Open a connection for a slot already counted in ``_created``."""
        try:
            conn = _sync.connect(on_connect=self.on_connect, **self._kwargs)
        except BaseException:
            with self._lock:
                self._created = max(0, self._created - 1)
            raise
        now = time.monotonic()
        with self._lock:
            closed = self._closed
            if closed:
                self._created = max(0, self._created - 1)
            else:
                self._checked_out.add(id(conn))
                self._checked_out_pc[id(conn)] = _SyncPooledConnection(conn, now, now, 1)
        if closed:
            self._close_quiet(conn)
            raise RuntimeError("Pool was closed while connecting")
        return conn

    def release(self, conn: Any) -> None:
        conn_id = id(conn)
        with self._lock:
            if conn_id not in self._checked_out:
                raise RuntimeError(
                    "Connection was not acquired from this pool or has already been released."
                )
            self._checked_out.discard(conn_id)
            pc = self._checked_out_pc.pop(conn_id)
            closed = self._closed

        async_conn = getattr(conn, "_conn", None)
        if async_conn is not None:
            _clear_stale_cursor(async_conn)
        reusable = async_conn is None or async_conn._is_reusable()
        if (
            not closed
            and reusable
            and async_conn is not None
            and not async_conn.autocommit
            and async_conn.in_transaction
        ):
            # Rollback is network I/O, so it runs outside _lock.
            try:
                conn.rollback()
            except Exception as exc:
                self._log.debug(
                    "Error rolling back sync connection on release: %s",
                    exc,
                    exc_info=True,
                )

        with self._lock:
            keep = not self._closed and reusable
            if keep:
                pc.last_used = time.monotonic()
                self._pool.append(pc)
            else:
                # Never hand a closed or desynced connection to the next caller.
                self._created = max(0, self._created - 1)
        if not keep:
            self._log.debug("Dropping connection on release (pool closed or unusable)")
            self._close_quiet(conn)
        self._sem.release()

    @contextmanager
    def connection(self) -> Generator[Any, None, None]:
        conn = self.acquire()
        try:
            yield conn
        finally:
            self.release(conn)

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "type":           "SyncPool",
                "pool_min":       self.min_size,
                "pool_max":       self.max_size,
                "pool_size":      self._created,
                "pool_available": len(self._pool),
                "pool_in_use":    len(self._checked_out),
                "pool_closed":    self._closed,
            }

    def close_all(self) -> None:
        self._stop_event.set()
        with self._lock:
            self._closed = True
            idle = list(self._pool)
            self._pool.clear()
            in_use = list(self._checked_out_pc.values())
            self._created = 0
            self._checked_out.clear()
            self._checked_out_pc.clear()
            for _ in range(self.max_size):
                try:
                    self._sem.release()
                except ValueError:
                    pass
        # Close outside the lock: each close is a network round trip.
        for pc in idle:
            self._close_quiet(pc.conn)
        for pc in in_use:
            self._close_quiet(pc.conn)
        try:
            self._maintain_thread.join(timeout=2.0)
        except Exception as exc:
            self._log.debug(
                "Error joining maintain thread: %s", exc, exc_info=True
            )

    def __enter__(self) -> SyncPool:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close_all()


class NullPool:
    _log = logging.getLogger("nzpy_extended.NullPool")

    def __init__(self, on_connect: Callable[[Any], Any] | None = None, **conn_kwargs: Any) -> None:
        self._kwargs = conn_kwargs
        self._on_connect = on_connect

    def acquire(self) -> Any:
        return _sync.connect(on_connect=self._on_connect, **self._kwargs)

    def release(self, conn: Any) -> None:
        try:
            conn.close()
        except Exception as exc:
            self._log.debug(
                "Error closing NullPool connection: %s", exc, exc_info=True
            )

    @contextmanager
    def connection(self) -> Generator[Any, None, None]:
        conn = self.acquire()
        try:
            yield conn
        finally:
            self.release(conn)

    def get_stats(self) -> dict[str, str]:
        return {"type": "NullPool"}

    def close_all(self) -> None:
        pass

    def __enter__(self) -> NullPool:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class AsyncNullPool:
    _log = logging.getLogger("nzpy_extended.AsyncNullPool")

    def __init__(self, on_connect: Callable[[Connection], Any] | None = None, **conn_kwargs: Any) -> None:
        self._kwargs = conn_kwargs
        self._on_connect = on_connect

    async def acquire(self) -> Connection:
        conn = Connection()
        merged = dict(_CONNECT_DEFAULTS)
        merged.update(self._kwargs)
        await conn.connect(**merged)
        if self._on_connect is not None:
            result = self._on_connect(conn)
            if hasattr(result, '__await__'):
                await result
        return conn

    async def release(self, conn: Connection) -> None:
        try:
            await conn.close()
        except Exception as exc:
            self._log.debug(
                "Error closing AsyncNullPool connection: %s", exc, exc_info=True
            )

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[Connection, None]:
        conn = await self.acquire()
        try:
            yield conn
        finally:
            await self.release(conn)

    def get_stats(self) -> dict[str, str]:
        return {"type": "AsyncNullPool"}

    async def close_all(self) -> None:
        pass

    async def __aenter__(self) -> AsyncNullPool:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


__all__ = ["NzPool", "SyncPool", "NullPool", "AsyncNullPool"]
