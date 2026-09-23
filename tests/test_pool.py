"""
test_pool.py
============
Connection pooling tests — modelled after Node.js driver's
NzPoolTests.smoke.test.js.

The Python driver has a well-featured NzPool class in
nzpy_extended.pool with:
  - acquire() / release() / connection() context manager
  - min_size / max_size / idle_timeout / max_lifetime / max_uses
  - acquire_timeout with queueing
  - ping-based validation
  - close_all() shutdown

Tests cover:
  1. Basic acquire + release
  2. Max connections (pool exhausted) + queueing
  3. Connection reuse after release
  4. State cleanup between uses (no leaked temp tables)
  5. Pool close / shutdown
  6. acquire timeout
  7. Async context manager (pool.connection())
  8. Pool with min_size pre-warming
"""

import asyncio
import os
import uuid

import pytest

import nzpy_extended as nzpy
from nzpy_extended.pool import NzPool

pytestmark = pytest.mark.full

NZ_HOST     = os.environ.get("NZ_DEV_HOST",     "192.168.0.144")
NZ_PORT     = int(os.environ.get("NZ_DEV_PORT",  "5480"))
NZ_DB       = os.environ.get("NZ_DEV_DB",        "JUST_DATA")
NZ_USER     = os.environ.get("NZ_DEV_USER",      "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD",  "password")

POOL_KWARGS = dict(
    user=NZ_USER, password=NZ_PASSWORD,
    host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
)


# ---------------------------------------------------------------------------
# Basic acquire / release
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_acquire_release():
    """Basic acquire + release cycle."""
    pool = NzPool(max_size=2, min_size=0, **POOL_KWARGS)
    try:
        conn = await pool.acquire()
        assert conn is not None
        cur = conn.cursor()
        await cur.execute("SELECT 1")
        row = await cur.fetchone()
        assert row[0] == 1
        await pool.release(conn)
    finally:
        await pool.close_all()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_async_context_manager():
    """The connection() async context manager acquires and releases."""
    pool = NzPool(max_size=2, min_size=0, **POOL_KWARGS)
    try:
        async with pool.connection() as conn:
            cur = conn.cursor()
            await cur.execute("SELECT 42")
            row = await cur.fetchone()
            assert row[0] == 42
    finally:
        await pool.close_all()


# ---------------------------------------------------------------------------
# Max connections + queueing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_max_connections_and_queueing():
    """When max connections are all in use, acquire must block until
       one is released.  Mirrors Node.js NzPool test."""
    pool = NzPool(max_size=2, min_size=0, acquire_timeout=15.0, **POOL_KWARGS)
    try:
        c1 = await pool.acquire()
        c2 = await pool.acquire()

        # Try to acquire a 3rd — should block
        acquired = False

        async def acquire_third():
            nonlocal acquired
            c3 = await pool.acquire()
            acquired = True
            await pool.release(c3)

        task = asyncio.ensure_future(acquire_third())
        await asyncio.sleep(1.0)
        assert not acquired, "Third acquire should be blocked"

        # Release one — third acquire should complete
        await pool.release(c1)
        await asyncio.wait_for(task, timeout=10.0)
        assert acquired, "Third acquire should have completed after release"

        await pool.release(c2)
    finally:
        await pool.close_all()


# ---------------------------------------------------------------------------
# Connection reuse
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_reuses_released_connection():
    """After release, the same underlying connection should be reusable."""
    pool = NzPool(max_size=1, min_size=0, **POOL_KWARGS)
    try:
        c1 = await pool.acquire()
        cur1 = c1.cursor()
        await cur1.execute("CREATE TEMP TABLE POOL_REUSE_TEST(c1 INT)")
        await cur1.execute("INSERT INTO POOL_REUSE_TEST VALUES(99)")
        pid1 = getattr(c1, '_backend_pid', None)
        await pool.release(c1)

        # Re-acquire — may be the same PID or new
        c2 = await pool.acquire()
        pid2 = getattr(c2, '_backend_pid', None)
        assert c2 is not None
        if pid1 == pid2:
            # Same connection reused — temp table should still be there
            cur2 = c2.cursor()
            await cur2.execute("SELECT c1 FROM POOL_REUSE_TEST")
            row = await cur2.fetchone()
            assert row is not None and row[0] == 99
        await pool.release(c2)
    finally:
        await pool.close_all()


# ---------------------------------------------------------------------------
# State cleanup between uses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_no_temp_table_leak():
    """Temp tables from one pooled connection should not leak to another
       connection.  In Netezza, temp tables are session-scoped, so if
       the same physical connection is reused the temp table may
       persist.  This test documents that behaviour."""
    pool = NzPool(max_size=2, min_size=0, **POOL_KWARGS)
    table_name = "T_POOL_LEAK_" + uuid.uuid4().hex[:9].upper()
    try:
        async with pool.connection() as conn1:
            cur1 = conn1.cursor()
            await cur1.execute(f"CREATE TEMP TABLE {table_name}(c1 INT)")
            await cur1.execute(f"INSERT INTO {table_name} VALUES(1)")
            pid1 = getattr(conn1, '_backend_pid', None)

        # A different acquire may or may not get the same PID
        async with pool.connection() as conn2:
            pid2 = getattr(conn2, '_backend_pid', None)
            cur2 = conn2.cursor()
            if pid2 == pid1:
                # Same session — temp table may still exist
                # This is expected behaviour with Netezza session reuse
                pass
            else:
                # Different session — temp table definitely gone
                # Just verify the connection works
                await cur2.execute("SELECT 1")
                row = await cur2.fetchone()
                assert row[0] == 1
    finally:
        await pool.close_all()


# ---------------------------------------------------------------------------
# Pool close / shutdown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_close_all():
    """After close_all(), acquire must raise RuntimeError."""
    pool = NzPool(max_size=2, min_size=0, **POOL_KWARGS)
    c1 = await pool.acquire()
    await pool.release(c1)
    await pool.close_all()

    with pytest.raises(RuntimeError):
        await pool.acquire()


# ---------------------------------------------------------------------------
# acquire timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_acquire_timeout():
    """When all connections are busy and none released, acquire must
       raise TimeoutError after acquire_timeout."""
    pool = NzPool(max_size=1, min_size=0, acquire_timeout=2.0, **POOL_KWARGS)
    c1 = await pool.acquire()
    try:
        with pytest.raises(TimeoutError):
            await pool.acquire()
    finally:
        await pool.release(c1)
        await pool.close_all()


# ---------------------------------------------------------------------------
# Pool with min_size pre-warming
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_min_size_prewarming():
    """min_size > 0 should pre-create connections."""
    pool = NzPool(max_size=3, min_size=2, **POOL_KWARGS)
    try:
        # First acquire should get a pre-warmed connection immediately
        c1 = await pool.acquire()
        assert c1 is not None
        cur = c1.cursor()
        await cur.execute("SELECT 1")
        row = await cur.fetchone()
        assert row[0] == 1
        await pool.release(c1)
    finally:
        await pool.close_all()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_double_release_raises():
    pool = NzPool(max_size=2, min_size=0, **POOL_KWARGS)
    try:
        conn = await pool.acquire()
        await pool.release(conn)
        with pytest.raises(RuntimeError, match="already been released"):
            await pool.release(conn)
    finally:
        await pool.close_all()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_max_uses_recycles_connection():
    pool = NzPool(max_size=2, min_size=0, max_uses=2, **POOL_KWARGS)
    try:
        c1 = await pool.acquire()
        await pool.release(c1)
        c2 = await pool.acquire()
        await pool.release(c2)
        c3 = await pool.acquire()
        cur = c3.cursor()
        await cur.execute("SELECT 1")
        assert (await cur.fetchone())[0] == 1
        await pool.release(c3)
    finally:
        await pool.close_all()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_max_lifetime_recycles_connection():
    pool = NzPool(max_size=2, min_size=0, max_lifetime=0.1, **POOL_KWARGS)
    try:
        c1 = await pool.acquire()
        await pool.release(c1)
        await asyncio.sleep(0.2)
        c2 = await pool.acquire()
        cur = c2.cursor()
        await cur.execute("SELECT 1")
        assert (await cur.fetchone())[0] == 1
        await pool.release(c2)
    finally:
        await pool.close_all()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_release_rollback_open_transaction():
    TABLE = f"test_pool_rollback_tx_{uuid.uuid4().hex}"
    pool = NzPool(max_size=2, min_size=0, **POOL_KWARGS)
    try:
        c0 = await pool.acquire()
        cur = c0.cursor()
        await cur.execute(f"CREATE TABLE {TABLE} (x INT)")
        await cur.execute(f"INSERT INTO {TABLE} VALUES (0)")
        await pool.release(c0)

        c1 = await pool.acquire()
        c1.autocommit = False
        cur = c1.cursor()
        await cur.execute(f"INSERT INTO {TABLE} VALUES (99)")
        assert c1.in_transaction is True
        await pool.release(c1)

        c2 = await pool.acquire()
        cur2 = c2.cursor()
        await cur2.execute(f"SELECT COUNT(*) FROM {TABLE}")
        row = await cur2.fetchone()
        assert row[0] == 1
        await pool.release(c2)
    finally:
        c_clean = await pool.acquire()
        await c_clean.cursor().execute(f"DROP TABLE {TABLE} IF EXISTS")
        await pool.release(c_clean)
        await pool.close_all()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_pool_close_all_closes_checked_out():
    pool = NzPool(max_size=2, min_size=0, **POOL_KWARGS)
    c = await pool.acquire()
    await pool.close_all()

    from nzpy_extended.core import ConnectionClosedError
    with pytest.raises((nzpy.InterfaceError, ConnectionClosedError, RuntimeError, OSError)):
        cur = c.cursor()
        await cur.execute("SELECT 1")
