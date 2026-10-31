"""
Memory leak regression tests.

Verifies that Python objects (Decimals, row-lists) and RSS do not grow
when fetching large result sets or cancelling queries after partial reads.
Runs parametrized with native extension enabled and disabled, async and sync API.

Requires:
  - Live Netezza database (NZ_DEV_HOST, NZ_DEV_PORT, NZ_DEV_DB, NZ_DEV_USER, NZ_DEV_PASSWORD)
  - psutil (pip install psutil)
"""

from __future__ import annotations

import gc
from typing import Any, Literal

import pytest

from tests._memory_helpers import (
    FETCH_ROWS_DEFAULT,
    FETCHMANY_SIZE,
    ITERATIONS_CANCEL,
    ITERATIONS_FETCHALL,
    ITERATIONS_MULTIPLE_CANCEL,
    ITERATIONS_POOL,
    MAX_ASYNC_SYNC_GROWTH_DELTA_MB,
    MAX_RSS_GROWTH_CANCEL_MB,
    MAX_RSS_GROWTH_FETCHALL_MB,
    MAX_RSS_GROWTH_PER_ITER_CANCEL_MB,
    MAX_RSS_GROWTH_PER_ITER_FETCHALL_MB,
    SQL_FACTPRODUCT,
    assert_memory_stable,
    collect_snapshot,
    count_leaked_objects,
    run_isolated_rss_benchmark,
    rss_mb,
)

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.full,
    pytest.mark.observe,
    pytest.mark.timeout(600),
]


@pytest.fixture
def db_kwargs_mem() -> dict[str, Any]:
    """Connection kwargs for memory tests."""
    import os

    return {
        "user": os.environ.get("NZ_DEV_USER", "admin"),
        "password": os.environ.get("NZ_DEV_PASSWORD", "password"),
        "database": os.environ.get("NZ_DEV_DB", "JUST_DATA"),
        "host": os.environ.get("NZ_DEV_HOST", "192.168.0.144"),
        "port": int(os.environ.get("NZ_DEV_PORT", "5480")),
    }


async def _run_fetchall_async(conn: Any, sql: str, iterations: int) -> tuple[list[float], int, int]:
    rss_per_iter: list[float] = []
    for i in range(iterations):
        cur = conn.cursor()
        await cur.execute(sql)
        rows = await cur.fetchall()
        rowcnt = len(rows)
        await cur.close()
        del rows, cur

        rss, dec, rl = collect_snapshot()
        rss_per_iter.append(rss)
        print(f"  iter {i}: RSS={rss:.1f} MB  decimals={dec}  rowlists={rl}  rows={rowcnt}")

    dec_final, rl_final = count_leaked_objects()
    return rss_per_iter, dec_final, rl_final


def _run_fetchall_sync(conn: Any, sql: str, iterations: int) -> tuple[list[float], int, int]:
    rss_per_iter: list[float] = []
    for i in range(iterations):
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        rowcnt = len(rows)
        cur.close()
        del rows, cur

        rss, dec, rl = collect_snapshot()
        rss_per_iter.append(rss)
        print(f"  iter {i}: RSS={rss:.1f} MB  decimals={dec}  rowlists={rl}  rows={rowcnt}")

    dec_final, rl_final = count_leaked_objects()
    return rss_per_iter, dec_final, rl_final


async def _run_cancel_async(
    conn: Any,
    sql: str,
    iterations: int,
    fetch_rows: int,
) -> tuple[list[float], int, int]:
    rss_per_iter: list[float] = []
    for i in range(iterations):
        cur = conn.cursor()
        await cur.execute(sql)
        read = 0
        for _ in range(fetch_rows):
            row = await cur.fetchone()
            if row is None:
                break
            read += 1
        await conn.cancel()
        await cur.close()
        del cur

        rss, dec, rl = collect_snapshot()
        rss_per_iter.append(rss)
        print(f"  iter {i}: RSS={rss:.1f} MB  decimals={dec}  rowlists={rl}  fetched={read}")

    dec_final, rl_final = count_leaked_objects()
    return rss_per_iter, dec_final, rl_final


def _run_cancel_sync(
    conn: Any,
    sql: str,
    iterations: int,
    fetch_rows: int,
) -> tuple[list[float], int, int]:
    rss_per_iter: list[float] = []
    for i in range(iterations):
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchmany(fetch_rows)
        read = len(rows)
        del rows
        conn.cancel()
        cur.close()
        del cur

        rss, dec, rl = collect_snapshot()
        rss_per_iter.append(rss)
        print(f"  iter {i}: RSS={rss:.1f} MB  decimals={dec}  rowlists={rl}  fetched={read}")

    dec_final, rl_final = count_leaked_objects()
    return rss_per_iter, dec_final, rl_final


async def _run_fetchmany_async(
    conn: Any, sql: str, iterations: int, chunk: int
) -> tuple[list[float], int, int]:
    rss_per_iter: list[float] = []
    for i in range(iterations):
        cur = conn.cursor()
        await cur.execute(sql)
        total = 0
        while True:
            batch = await cur.fetchmany(chunk)
            if not batch:
                break
            total += len(batch)
            del batch
        await cur.close()
        del cur

        rss, dec, rl = collect_snapshot()
        rss_per_iter.append(rss)
        print(f"  iter {i}: RSS={rss:.1f} MB  decimals={dec}  rowlists={rl}  rows={total}")

    dec_final, rl_final = count_leaked_objects()
    return rss_per_iter, dec_final, rl_final


def _run_fetchmany_sync(
    conn: Any, sql: str, iterations: int, chunk: int
) -> tuple[list[float], int, int]:
    rss_per_iter: list[float] = []
    for i in range(iterations):
        cur = conn.cursor()
        cur.execute(sql)
        total = 0
        while True:
            batch = cur.fetchmany(chunk)
            if not batch:
                break
            total += len(batch)
            del batch
        cur.close()
        del cur

        rss, dec, rl = collect_snapshot()
        rss_per_iter.append(rss)
        print(f"  iter {i}: RSS={rss:.1f} MB  decimals={dec}  rowlists={rl}  rows={total}")

    dec_final, rl_final = count_leaked_objects()
    return rss_per_iter, dec_final, rl_final


async def _run_iterate_cancel_async(
    conn: Any, sql: str, iterations: int, fetch_rows: int
) -> tuple[list[float], int, int]:
    rss_per_iter: list[float] = []
    for i in range(iterations):
        cur = conn.cursor()
        await cur.execute(sql)
        read = 0
        async for row in cur:
            del row
            read += 1
            if read >= fetch_rows:
                break
        await conn.cancel()
        await cur.close()
        del cur

        rss, dec, rl = collect_snapshot()
        rss_per_iter.append(rss)
        print(f"  iter {i}: RSS={rss:.1f} MB  decimals={dec}  rowlists={rl}  iterated={read}")

    dec_final, rl_final = count_leaked_objects()
    return rss_per_iter, dec_final, rl_final


async def _run_close_without_cancel_async(
    conn: Any, sql: str, iterations: int, fetch_rows: int
) -> tuple[list[float], int, int]:
    rss_per_iter: list[float] = []
    for i in range(iterations):
        cur = conn.cursor()
        await cur.execute(sql)
        read = 0
        for _ in range(fetch_rows):
            row = await cur.fetchone()
            if row is None:
                break
            read += 1
        await cur.close()
        del cur

        rss, dec, rl = collect_snapshot()
        rss_per_iter.append(rss)
        print(f"  iter {i}: RSS={rss:.1f} MB  decimals={dec}  rowlists={rl}  fetched={read}")

    dec_final, rl_final = count_leaked_objects()
    return rss_per_iter, dec_final, rl_final


def _run_close_without_cancel_sync(
    conn: Any, sql: str, iterations: int, fetch_rows: int
) -> tuple[list[float], int, int]:
    rss_per_iter: list[float] = []
    for i in range(iterations):
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchmany(fetch_rows)
        read = len(rows)
        del rows
        cur.close()
        del cur

        rss, dec, rl = collect_snapshot()
        rss_per_iter.append(rss)
        print(f"  iter {i}: RSS={rss:.1f} MB  decimals={dec}  rowlists={rl}  fetched={read}")

    dec_final, rl_final = count_leaked_objects()
    return rss_per_iter, dec_final, rl_final


async def _run_pool_cycles(
    db_kwargs: dict[str, Any], sql: str, iterations: int
) -> tuple[list[float], int, int]:
    from nzpy_extended.pool import NzPool

    pool = NzPool(min_size=2, max_size=4, acquire_timeout=30.0, **db_kwargs)
    await pool.open()
    rss_per_iter: list[float] = []
    try:
        for i in range(iterations):
            conn = await pool.acquire()
            try:
                cur = conn.cursor()
                await cur.execute(sql)
                read = 0
                for _ in range(FETCH_ROWS_DEFAULT):
                    row = await cur.fetchone()
                    if row is None:
                        break
                    read += 1
                await conn.cancel()
                await cur.close()
            finally:
                await pool.release(conn)

            rss, dec, rl = collect_snapshot()
            rss_per_iter.append(rss)
            print(f"  iter {i}: RSS={rss:.1f} MB  decimals={dec}  rowlists={rl}  fetched={read}")
    finally:
        await pool.close_all()

    dec_final, rl_final = count_leaked_objects()
    return rss_per_iter, dec_final, rl_final


async def _run_new_connection_each_async(
    db_kwargs: dict[str, Any], sql: str, iterations: int
) -> tuple[list[float], int, int]:
    import nzpy_extended as nzpy

    rss_per_iter: list[float] = []
    for i in range(iterations):
        conn = await nzpy.connect(**db_kwargs)
        try:
            cur = conn.cursor()
            await cur.execute(sql)
            rows = await cur.fetchall()
            rowcnt = len(rows)
            await cur.close()
            del rows, cur
        finally:
            await conn.close()

        rss, dec, rl = collect_snapshot()
        rss_per_iter.append(rss)
        print(f"  iter {i}: RSS={rss:.1f} MB  decimals={dec}  rowlists={rl}  rows={rowcnt}")

    dec_final, rl_final = count_leaked_objects()
    return rss_per_iter, dec_final, rl_final


def _run_new_connection_each_sync(
    db_kwargs: dict[str, Any], sql: str, iterations: int
) -> tuple[list[float], int, int]:
    import nzpy_extended.sync as nzpy_sync

    rss_per_iter: list[float] = []
    for i in range(iterations):
        conn = nzpy_sync.connect(**db_kwargs)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            rowcnt = len(rows)
            cur.close()
            del rows, cur
        finally:
            conn.close()

        rss, dec, rl = collect_snapshot()
        rss_per_iter.append(rss)
        print(f"  iter {i}: RSS={rss:.1f} MB  decimals={dec}  rowlists={rl}  rows={rowcnt}")

    dec_final, rl_final = count_leaked_objects()
    return rss_per_iter, dec_final, rl_final


@pytest.mark.parametrize("api", ["async", "sync"], ids=["async", "sync"])
def test_no_memory_leak_repeated_fetchall(
    api: Literal["async", "sync"],
    native_mode: bool,
    db_kwargs_mem: dict[str, Any],
) -> None:
    """Repeated fetchall on large SELECT must not leak objects or RSS."""
    gc.collect()
    rss0 = rss_mb()
    dec0, rl0 = count_leaked_objects()
    print(f"\n[{api=} {native_mode=}]  init: RSS={rss0:.1f} MB  decimals={dec0}  rowlists={rl0}")

    if api == "async":
        import nzpy_extended as nzpy

        async def _run() -> tuple[list[float], int, int]:
            conn = await nzpy.connect(**db_kwargs_mem)
            try:
                return await _run_fetchall_async(conn, SQL_FACTPRODUCT, ITERATIONS_FETCHALL)
            finally:
                await conn.close()

        import asyncio

        rss_per_iter, dec_final, rl_final = asyncio.run(_run())
    else:
        import nzpy_extended.sync as nzpy_sync

        conn = nzpy_sync.connect(**db_kwargs_mem)
        try:
            rss_per_iter, dec_final, rl_final = _run_fetchall_sync(
                conn, SQL_FACTPRODUCT, ITERATIONS_FETCHALL
            )
        finally:
            conn.close()

    gc.collect()
    print(f"[{api=} {native_mode=}]  final: decimals={dec_final}  rowlists={rl_final}")

    assert_memory_stable(
        rss_per_iter,
        dec_final=dec_final,
        rl_final=rl_final,
        max_rss_growth_mb=MAX_RSS_GROWTH_FETCHALL_MB,
        max_rss_growth_per_iter_mb=MAX_RSS_GROWTH_PER_ITER_FETCHALL_MB,
        iterations=ITERATIONS_FETCHALL,
        label=f"{api}/native={native_mode}",
    )


@pytest.mark.parametrize("api", ["async", "sync"], ids=["async", "sync"])
def test_no_memory_leak_cancel_after_partial_fetch(
    api: Literal["async", "sync"],
    native_mode: bool,
    db_kwargs_mem: dict[str, Any],
) -> None:
    """Cancel after reading a few rows must not leak memory across iterations."""
    gc.collect()
    rss0 = rss_mb()
    dec0, rl0 = count_leaked_objects()
    print(f"\n[{api=} {native_mode=}]  init: RSS={rss0:.1f} MB  decimals={dec0}  rowlists={rl0}")

    if api == "async":
        import nzpy_extended as nzpy

        async def _run() -> tuple[list[float], int, int]:
            conn = await nzpy.connect(**db_kwargs_mem)
            try:
                return await _run_cancel_async(
                    conn, SQL_FACTPRODUCT, ITERATIONS_CANCEL, FETCH_ROWS_DEFAULT
                )
            finally:
                await conn.close()

        import asyncio

        rss_per_iter, dec_final, rl_final = asyncio.run(_run())
    else:
        import nzpy_extended.sync as nzpy_sync

        conn = nzpy_sync.connect(**db_kwargs_mem)
        try:
            rss_per_iter, dec_final, rl_final = _run_cancel_sync(
                conn, SQL_FACTPRODUCT, ITERATIONS_CANCEL, FETCH_ROWS_DEFAULT
            )
        finally:
            conn.close()

    gc.collect()
    print(f"[{api=} {native_mode=}]  final: decimals={dec_final}  rowlists={rl_final}")

    assert_memory_stable(
        rss_per_iter,
        dec_final=dec_final,
        rl_final=rl_final,
        max_rss_growth_mb=MAX_RSS_GROWTH_CANCEL_MB,
        max_rss_growth_per_iter_mb=MAX_RSS_GROWTH_PER_ITER_CANCEL_MB,
        iterations=ITERATIONS_CANCEL,
        label=f"{api}/native={native_mode}/cancel",
    )


@pytest.mark.parametrize("api", ["async", "sync"], ids=["async", "sync"])
def test_no_memory_leak_multiple_cancels_same_connection(
    api: Literal["async", "sync"],
    native_mode: bool,
    db_kwargs_mem: dict[str, Any],
) -> None:
    """Many cancel cycles on one connection must stay stable; session remains usable."""
    gc.collect()
    print(f"\n[{api=} {native_mode=}]  multiple cancel test")

    if api == "async":
        import nzpy_extended as nzpy

        async def _run() -> tuple[list[float], int, int]:
            conn = await nzpy.connect(**db_kwargs_mem)
            try:
                rss_per_iter, dec_final, rl_final = await _run_cancel_async(
                    conn, SQL_FACTPRODUCT, ITERATIONS_MULTIPLE_CANCEL, FETCH_ROWS_DEFAULT
                )
                cur = conn.cursor()
                await cur.execute("SELECT 1")
                row = await cur.fetchone()
                await cur.close()
                assert row is not None and int(row[0]) == 1
                return rss_per_iter, dec_final, rl_final
            finally:
                await conn.close()

        import asyncio

        rss_per_iter, dec_final, rl_final = asyncio.run(_run())
    else:
        import nzpy_extended.sync as nzpy_sync

        conn = nzpy_sync.connect(**db_kwargs_mem)
        try:
            rss_per_iter, dec_final, rl_final = _run_cancel_sync(
                conn, SQL_FACTPRODUCT, ITERATIONS_MULTIPLE_CANCEL, FETCH_ROWS_DEFAULT
            )
            cur = conn.cursor()
            cur.execute("SELECT 1")
            row = cur.fetchone()
            cur.close()
            assert row is not None and int(row[0]) == 1
        finally:
            conn.close()

    gc.collect()
    print(f"[{api=} {native_mode=}]  final: decimals={dec_final}  rowlists={rl_final}")

    assert_memory_stable(
        rss_per_iter,
        dec_final=dec_final,
        rl_final=rl_final,
        max_rss_growth_mb=MAX_RSS_GROWTH_CANCEL_MB,
        max_rss_growth_per_iter_mb=MAX_RSS_GROWTH_PER_ITER_CANCEL_MB,
        iterations=ITERATIONS_MULTIPLE_CANCEL,
        label=f"{api}/native={native_mode}/multi-cancel",
    )


@pytest.mark.parametrize("api", ["async", "sync"], ids=["async", "sync"])
def test_no_memory_leak_fetchmany_chunks(
    api: Literal["async", "sync"],
    native_mode: bool,
    db_kwargs_mem: dict[str, Any],
) -> None:
    """fetchmany() in chunks must not leak (exercises streaming read path)."""
    gc.collect()
    print(f"\n[{api=} {native_mode=}]  fetchmany chunks test")

    if api == "async":
        import asyncio
        import nzpy_extended as nzpy

        async def _run() -> tuple[list[float], int, int]:
            conn = await nzpy.connect(**db_kwargs_mem)
            try:
                return await _run_fetchmany_async(
                    conn, SQL_FACTPRODUCT, ITERATIONS_FETCHALL, FETCHMANY_SIZE
                )
            finally:
                await conn.close()

        rss_per_iter, dec_final, rl_final = asyncio.run(_run())
    else:
        import nzpy_extended.sync as nzpy_sync

        conn = nzpy_sync.connect(**db_kwargs_mem)
        try:
            rss_per_iter, dec_final, rl_final = _run_fetchmany_sync(
                conn, SQL_FACTPRODUCT, ITERATIONS_FETCHALL, FETCHMANY_SIZE
            )
        finally:
            conn.close()

    assert_memory_stable(
        rss_per_iter,
        dec_final=dec_final,
        rl_final=rl_final,
        max_rss_growth_mb=MAX_RSS_GROWTH_FETCHALL_MB,
        max_rss_growth_per_iter_mb=MAX_RSS_GROWTH_PER_ITER_FETCHALL_MB,
        iterations=ITERATIONS_FETCHALL,
        label=f"{api}/native={native_mode}/fetchmany",
    )


def test_no_memory_leak_async_iteration_cancel(
    native_mode: bool,
    db_kwargs_mem: dict[str, Any],
) -> None:
    """async for partial read + cancel must not leak."""
    gc.collect()
    print(f"\n[async iterate cancel {native_mode=}]")

    import asyncio
    import nzpy_extended as nzpy

    async def _run() -> tuple[list[float], int, int]:
        conn = await nzpy.connect(**db_kwargs_mem)
        try:
            return await _run_iterate_cancel_async(
                conn, SQL_FACTPRODUCT, ITERATIONS_CANCEL, FETCH_ROWS_DEFAULT
            )
        finally:
            await conn.close()

    rss_per_iter, dec_final, rl_final = asyncio.run(_run())

    assert_memory_stable(
        rss_per_iter,
        dec_final=dec_final,
        rl_final=rl_final,
        max_rss_growth_mb=MAX_RSS_GROWTH_CANCEL_MB,
        max_rss_growth_per_iter_mb=MAX_RSS_GROWTH_PER_ITER_CANCEL_MB,
        iterations=ITERATIONS_CANCEL,
        label=f"async-iterate/native={native_mode}/cancel",
    )


@pytest.mark.parametrize("api", ["async", "sync"], ids=["async", "sync"])
def test_no_memory_leak_close_without_cancel(
    api: Literal["async", "sync"],
    native_mode: bool,
    db_kwargs_mem: dict[str, Any],
) -> None:
    """Closing cursor after partial read (no explicit cancel) must not leak."""
    gc.collect()
    print(f"\n[{api=} {native_mode=}]  close-without-cancel test")

    if api == "async":
        import asyncio
        import nzpy_extended as nzpy

        async def _run() -> tuple[list[float], int, int]:
            conn = await nzpy.connect(**db_kwargs_mem)
            try:
                return await _run_close_without_cancel_async(
                    conn, SQL_FACTPRODUCT, ITERATIONS_CANCEL, FETCH_ROWS_DEFAULT
                )
            finally:
                await conn.close()

        rss_per_iter, dec_final, rl_final = asyncio.run(_run())
    else:
        import nzpy_extended.sync as nzpy_sync

        conn = nzpy_sync.connect(**db_kwargs_mem)
        try:
            rss_per_iter, dec_final, rl_final = _run_close_without_cancel_sync(
                conn, SQL_FACTPRODUCT, ITERATIONS_CANCEL, FETCH_ROWS_DEFAULT
            )
        finally:
            conn.close()

    assert_memory_stable(
        rss_per_iter,
        dec_final=dec_final,
        rl_final=rl_final,
        max_rss_growth_mb=MAX_RSS_GROWTH_CANCEL_MB,
        max_rss_growth_per_iter_mb=MAX_RSS_GROWTH_PER_ITER_CANCEL_MB,
        iterations=ITERATIONS_CANCEL,
        label=f"{api}/native={native_mode}/close-no-cancel",
    )


def test_no_memory_leak_pool_cancel_cycles(
    native_mode: bool,
    db_kwargs_mem: dict[str, Any],
) -> None:
    """Pooled connections: partial fetch + cancel + release must not leak."""
    gc.collect()
    print(f"\n[pool cancel {native_mode=}]")

    import asyncio

    rss_per_iter, dec_final, rl_final = asyncio.run(
        _run_pool_cycles(db_kwargs_mem, SQL_FACTPRODUCT, ITERATIONS_POOL)
    )

    assert_memory_stable(
        rss_per_iter,
        dec_final=dec_final,
        rl_final=rl_final,
        max_rss_growth_mb=MAX_RSS_GROWTH_CANCEL_MB,
        max_rss_growth_per_iter_mb=MAX_RSS_GROWTH_PER_ITER_CANCEL_MB,
        iterations=ITERATIONS_POOL,
        label=f"pool/native={native_mode}/cancel",
    )


@pytest.mark.parametrize("api", ["async", "sync"], ids=["async", "sync"])
def test_no_memory_leak_new_connection_each_iteration(
    api: Literal["async", "sync"],
    native_mode: bool,
    db_kwargs_mem: dict[str, Any],
) -> None:
    """Fresh connection per iteration + full fetchall must not leak."""
    gc.collect()
    print(f"\n[{api=} {native_mode=}]  new-conn-each-iter test")

    if api == "async":
        import asyncio

        rss_per_iter, dec_final, rl_final = asyncio.run(
            _run_new_connection_each_async(db_kwargs_mem, SQL_FACTPRODUCT, ITERATIONS_FETCHALL)
        )
    else:
        rss_per_iter, dec_final, rl_final = _run_new_connection_each_sync(
            db_kwargs_mem, SQL_FACTPRODUCT, ITERATIONS_FETCHALL
        )

    assert_memory_stable(
        rss_per_iter,
        dec_final=dec_final,
        rl_final=rl_final,
        max_rss_growth_mb=MAX_RSS_GROWTH_FETCHALL_MB,
        max_rss_growth_per_iter_mb=MAX_RSS_GROWTH_PER_ITER_FETCHALL_MB,
        iterations=ITERATIONS_FETCHALL,
        label=f"{api}/native={native_mode}/new-conn",
    )


@pytest.mark.parametrize("mode", ["fetchall", "cancel"], ids=["fetchall", "cancel"])
def test_async_vs_sync_rss_comparable(mode: str) -> None:
    """Async and sync must have similar RSS growth in isolated subprocesses."""
    if mode == "fetchall":
        iters = ITERATIONS_FETCHALL
        max_growth = MAX_RSS_GROWTH_FETCHALL_MB
        max_rate = MAX_RSS_GROWTH_PER_ITER_FETCHALL_MB
    else:
        iters = ITERATIONS_CANCEL
        max_growth = MAX_RSS_GROWTH_CANCEL_MB
        max_rate = MAX_RSS_GROWTH_PER_ITER_CANCEL_MB

    async_rss = run_isolated_rss_benchmark(
        "async", mode, iters, SQL_FACTPRODUCT,
        fetch_rows=FETCH_ROWS_DEFAULT, native_on=True,
    )
    sync_rss = run_isolated_rss_benchmark(
        "sync", mode, iters, SQL_FACTPRODUCT,
        fetch_rows=FETCH_ROWS_DEFAULT, native_on=True,
    )

    async_growth = async_rss[-1] - async_rss[0]
    sync_growth = sync_rss[-1] - sync_rss[0]
    delta = async_growth - sync_growth

    print(
        f"\n[async_vs_sync {mode}] async={async_growth:+.1f} MB  "
        f"sync={sync_growth:+.1f} MB  delta={delta:+.1f} MB"
    )

    assert abs(delta) < MAX_ASYNC_SYNC_GROWTH_DELTA_MB, (
        f"async vs sync RSS growth differ by {delta:+.1f} MB "
        f"(limit {MAX_ASYNC_SYNC_GROWTH_DELTA_MB} MB) in mode={mode}"
    )

    assert_memory_stable(
        async_rss,
        dec_final=0,
        rl_final=0,
        max_rss_growth_mb=max_growth,
        max_rss_growth_per_iter_mb=max_rate,
        iterations=iters,
        label=f"isolated-async/{mode}",
    )
    assert_memory_stable(
        sync_rss,
        dec_final=0,
        rl_final=0,
        max_rss_growth_mb=max_growth,
        max_rss_growth_per_iter_mb=max_rate,
        iterations=iters,
        label=f"isolated-sync/{mode}",
    )
