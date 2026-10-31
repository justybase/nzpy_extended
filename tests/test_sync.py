import asyncio
import inspect
import sys
import os
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import nzpy_extended as nzpy
import nzpy_extended.sync as sync_nzpy
from nzpy_extended.pool import SyncPool, NullPool, AsyncNullPool

pytestmark = [pytest.mark.smoke, pytest.mark.full]

HEAVY_SQL = """
    /*OPIS:test_sync_timeout*/
    SELECT F1.PRODUCTKEY, COUNT(DISTINCT (F1.PRODUCTKEY / F2.PRODUCTKEY))
    FROM
    ( SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY LIMIT 30000) F1,
    ( SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY LIMIT 30000) F2
    GROUP BY 1
    LIMIT 500
"""


@pytest.fixture
def synccon(db_kwargs_fn):
    """Fresh sync connection, closed after every test."""
    conn = sync_nzpy.connect(**db_kwargs_fn)
    yield conn
    try:
        conn.close()
    except Exception:
        pass


def _drop_table(conn, table_name):
    """Safely drop a table, ignoring errors if it doesn't exist."""
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE {table_name}")
    except Exception:
        pass


class TestSyncBasic:
    """Test 1: sync API (basic operations)."""

    TABLE = "test_nzpy_sync_basic"

    def test_execute_fetchone(self, synccon):
        with synccon.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 1

    def test_connection_execute_oneshot(self, synccon):
        row = synccon.execute("SELECT 1").fetchone()
        assert row[0] == 1

    def test_connection_execute_with_params(self, synccon):
        _drop_table(synccon, self.TABLE)
        synccon.execute(f"CREATE TABLE {self.TABLE} (id INT)")
        synccon.execute(f"INSERT INTO {self.TABLE} VALUES (?)", (42,))
        row = synccon.execute(f"SELECT id FROM {self.TABLE}").fetchone()
        assert row[0] == 42
        _drop_table(synccon, self.TABLE)

    @pytest.mark.asyncio
    async def test_connection_execute_async(self, con):
        row = await (await con.execute("SELECT 2")).fetchone()
        assert row[0] == 2

    def test_connection_context_manager(self, db_kwargs_fn):
        with sync_nzpy.connect(**db_kwargs_fn) as conn:
            _drop_table(conn, self.TABLE)
            with conn.cursor() as cur:
                cur.execute(f"CREATE TABLE {self.TABLE} (id INT, name VARCHAR(50))")
                cur.execute(f"INSERT INTO {self.TABLE} VALUES (?, ?)", (1, "Alice"))
                cur.execute(f"SELECT * FROM {self.TABLE} WHERE id = 1")
                row = cur.fetchone()
                assert row[0] == 1
                assert row[1] == "Alice"
            _drop_table(conn, self.TABLE)

    def test_cleanup(self, db_kwargs_fn):
        with sync_nzpy.connect(**db_kwargs_fn) as conn:
            _drop_table(conn, self.TABLE)


class TestSyncIteration:
    """Test 3: cursor iteration (__iter__)."""

    TABLE = "test_nzpy_sync_iter"

    def test_cursor_iteration(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {self.TABLE} (id INT, name VARCHAR(50))")
            cur.execute(f"INSERT INTO {self.TABLE} VALUES (?, ?)", (1, "Alice"))
            cur.execute(f"INSERT INTO {self.TABLE} VALUES (?, ?)", (2, "Bob"))

        with synccon.cursor() as cur:
            cur.execute(f"SELECT * FROM {self.TABLE} ORDER BY id")
            rows = []
            for row in cur:
                rows.append(row)
            assert len(rows) == 2
            assert rows[0][0] == 1
            assert rows[1][0] == 2
        _drop_table(synccon, self.TABLE)

    def test_cleanup(self, synccon):
        _drop_table(synccon, self.TABLE)


class TestSyncRunnerRecovery:
    """Test _SyncRunner auto-restart after event loop death."""

    def test_runner_recovers_after_loop_stopped(self, db_kwargs_fn):
        from nzpy_extended._runner import runner  # pyright: ignore[reportPrivateUsage]

        runner.close()

        conn = sync_nzpy.connect(**db_kwargs_fn)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                assert cur.fetchone()[0] == 1
        finally:
            conn.close()

    def test_runner_still_usable_after_recovery(self, db_kwargs_fn):
        from nzpy_extended._runner import runner  # pyright: ignore[reportPrivateUsage]

        runner.close()
        conn1 = sync_nzpy.connect(**db_kwargs_fn)
        conn1.close()
        conn2 = sync_nzpy.connect(**db_kwargs_fn)
        try:
            with conn2.cursor() as cur:
                cur.execute("SELECT 2")
                assert cur.fetchone()[0] == 2
        finally:
            conn2.close()


class TestSyncPool:
    """Test 4: SyncPool."""

    def test_pool_acquire_release(self, db_kwargs_fn):
        pool = SyncPool(min_size=1, max_size=5, **db_kwargs_fn)
        try:
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            stats = pool.get_stats()
            assert stats["pool_size"] >= 1
            assert stats["pool_in_use"] == 0
        finally:
            pool.close_all()

    def test_pool_context_manager(self, db_kwargs_fn):
        with SyncPool(min_size=1, max_size=5, **db_kwargs_fn) as pool:
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            stats = pool.get_stats()
            assert stats["pool_closed"] is False

    def test_pool_stats(self, db_kwargs_fn):
        pool = SyncPool(min_size=2, max_size=5, **db_kwargs_fn)
        try:
            stats = pool.get_stats()
            assert "pool_min" in stats
            assert "pool_max" in stats
            assert "pool_size" in stats
            assert "pool_available" in stats
            assert "pool_in_use" in stats
            assert "pool_closed" in stats
            assert stats["pool_min"] == 2
            assert stats["pool_closed"] is False
        finally:
            pool.close_all()

    def test_pool_release_rollback_open_transaction(self, db_kwargs_fn, synccon):
        TABLE = "test_syncpool_rollback_tx"
        _drop_table(synccon, TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {TABLE} (x INT)")
            cur.execute(f"INSERT INTO {TABLE} VALUES (0)")

        pool = SyncPool(min_size=1, max_size=5, **db_kwargs_fn)
        try:
            conn1 = pool.acquire()
            conn1.autocommit = False
            cur = conn1.cursor()
            cur.execute(f"INSERT INTO {TABLE} VALUES (99)")
            pool.release(conn1)

            conn2 = pool.acquire()
            cur2 = conn2.cursor()
            cur2.execute(f"SELECT COUNT(*) FROM {TABLE}")
            row = cur2.fetchone()
            assert row[0] == 1, f"Expected 1 row (the pre-existing one), got {row[0]}"
            pool.release(conn2)
        finally:
            pool.close_all()
            _drop_table(synccon, TABLE)

    def test_close_all_closes_checked_out(self, db_kwargs_fn):
        pool = SyncPool(min_size=1, max_size=5, **db_kwargs_fn)
        c = pool.acquire()
        pool.close_all()

        from nzpy_extended.core import ConnectionClosedError
        with pytest.raises((nzpy.InterfaceError, ConnectionClosedError, RuntimeError, OSError)):
            cur = c.cursor()
            cur.execute("SELECT 1")


class TestNullPool:
    """Test 5: NullPool."""

    def test_nullpool(self, db_kwargs_fn):
        pool = NullPool(**db_kwargs_fn)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        stats = pool.get_stats()
        assert stats["type"] == "NullPool"

    def test_nullpool_context_manager(self, db_kwargs_fn):
        with NullPool(**db_kwargs_fn) as pool:
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")


class TestAsyncRegression:
    """Test 6: async API still works (no regression)."""

    @pytest.mark.asyncio
    async def test_async_connect_and_query(self, db_kwargs_fn):
        async with await nzpy.connect(**db_kwargs_fn) as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                row = await cur.fetchone()
                assert row is not None
                assert row[0] == 1

    @pytest.mark.asyncio
    async def test_async_context_manager_on_close(self, db_kwargs_fn):
        conn = await nzpy.connect(**db_kwargs_fn)
        cur = conn.cursor()
        await cur.execute("SELECT 1")
        assert (await cur.fetchone()) is not None
        await conn.close()

    @pytest.mark.asyncio
    async def test_asyncnullpool(self, db_kwargs_fn):
        pool = AsyncNullPool(**db_kwargs_fn)
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                row = await cur.fetchone()
                assert row is not None
        stats = pool.get_stats()
        assert stats["type"] == "AsyncNullPool"


class TestPortDefaults:
    """Test 7: default port is 5480."""

    def test_async_connect_port_default(self):
        sig_async = inspect.signature(nzpy.connect)
        assert sig_async.parameters["port"].default == 5480

    def test_sync_connect_port_default(self):
        sig_sync = inspect.signature(sync_nzpy.connect)
        assert sig_sync.parameters["port"].default == 5480


class TestSyncExecutemany:
    """Test executemany on sync cursor."""

    TABLE = "test_nzpy_sync_emany"

    def test_executemany(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {self.TABLE} (id INT, name VARCHAR(50))")
            cur.executemany(
                f"INSERT INTO {self.TABLE} VALUES (?, ?)",
                [(1, "Alice"), (2, "Bob"), (3, "Charlie")]
            )
            cur.execute(f"SELECT COUNT(*) FROM {self.TABLE}")
            row = cur.fetchone()
            assert row[0] == 3
        _drop_table(synccon, self.TABLE)

    def test_executemany_partial_failure_preserves_rowcount(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {self.TABLE} (id INT)")
            with pytest.raises(nzpy.Error) as info:
                cur.executemany(
                    f"INSERT INTO {self.TABLE} VALUES (?)",
                    [(1,), (2,), ("not_an_int",), (4,), (5,)]
                )
            assert info.value.__notes__ == ["executemany failed at param set 3/5"]
            assert cur.rowcount == 2
        _drop_table(synccon, self.TABLE)

    def test_executemany_partial_failure_message_contains_index(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {self.TABLE} (id INT)")
            with pytest.raises(nzpy.Error) as info:
                cur.executemany(
                    f"INSERT INTO {self.TABLE} VALUES (?)",
                    [(1,), ("bad_int",), (3,)]
                )
            assert info.value.__notes__ == ["executemany failed at param set 2/3"]
            assert cur.rowcount == 1
        _drop_table(synccon, self.TABLE)

    @pytest.mark.asyncio
    async def test_executemany_partial_failure_async(self, con):
        TABLE = "test_async_emany_pf"
        cur = con.cursor()
        await cur.execute(f"CREATE TABLE {TABLE} (id INT)")
        try:
            with pytest.raises(nzpy.Error) as info:
                await cur.executemany(
                    f"INSERT INTO {TABLE} VALUES (?)",
                    [(1,), ("bad",), (3,)]
                )
            assert info.value.__notes__ == ["executemany failed at param set 2/3"]
            assert cur.rowcount == 1
        finally:
            await cur.execute(f"DROP TABLE {TABLE}")

    def test_cleanup(self, synccon):
        _drop_table(synccon, self.TABLE)


class TestSyncFetchMany:
    """Test fetchmany on sync cursor."""

    TABLE = "test_nzpy_sync_fmany"

    def test_fetchmany(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {self.TABLE} (id INT)")
            for i in range(5):
                cur.execute(f"INSERT INTO {self.TABLE} VALUES (?)", (i,))
            cur.execute(f"SELECT * FROM {self.TABLE} ORDER BY id")
            rows = cur.fetchmany(3)
            assert len(rows) == 3
            more = cur.fetchmany(10)
            assert len(more) == 2
        _drop_table(synccon, self.TABLE)

    def test_cleanup(self, synccon):
        _drop_table(synccon, self.TABLE)


class TestSyncDescription:
    """Test description property on sync cursor."""

    def test_description(self, synccon):
        with synccon.cursor() as cur:
            cur.execute("SELECT 1 AS col1, 2 AS col2")
            desc = cur.description
            assert desc is not None
            assert len(desc) == 2
            assert desc[0][0].upper() == "COL1"
            assert desc[1][0].upper() == "COL2"

    def test_description_before_fetch(self, synccon):
        state = []
        with synccon.cursor() as cur:
            cur.execute("SELECT 1 AS val")
            desc_before = cur.description
            state.append(("before", desc_before))
            row = cur.fetchone()
            desc_after = cur.description
            state.append(("after", desc_after))
        assert state[0][1] is not None, "description should be available after execute"
        assert len(state[0][1]) == 1
        assert state[0][1][0][0].upper() == "VAL"
        assert state[1][1] is not None, "description should persist after fetchone"
        assert state[1][1] == state[0][1], "description content should be stable"

    def test_description_none_for_insert(self, synccon):
        TABLE = "test_desc_insert"
        _drop_table(synccon, TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {TABLE} (id INT)")
            cur.execute(f"INSERT INTO {TABLE} VALUES (1)")
            assert cur.description is None
        _drop_table(synccon, TABLE)

    def test_description_none_for_ddl(self, synccon):
        TABLE = "test_desc_ddl"
        _drop_table(synccon, TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {TABLE} (id INT)")
            assert cur.description is None
        _drop_table(synccon, TABLE)

    def test_description_after_multiple_executes(self, synccon):
        with synccon.cursor() as cur:
            cur.execute("SELECT 1 AS a")
            desc1 = cur.description
            cur.execute("SELECT 2 AS b, 3 AS c")
            desc2 = cur.description
            assert desc1 is not None and desc1[0][0].upper() == "A"
            assert desc2 is not None and len(desc2) == 2
            assert desc2[0][0].upper() == "B"

    def test_description_empty_result_set_has_columns(self, synccon):
        TABLE = "test_desc_empty"
        _drop_table(synccon, TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {TABLE} (id INT)")
            cur.execute(f"SELECT * FROM {TABLE} WHERE 1=0")
            desc = cur.description
            assert desc is not None
            assert len(desc) == 1
            assert desc[0][0].upper() == "ID"
            rows = cur.fetchall()
            assert rows == []
        _drop_table(synccon, TABLE)

    @pytest.mark.asyncio
    async def test_description_async_before_fetch(self, con):
        cur = con.cursor()
        await cur.execute("SELECT 1 AS val")
        desc = cur.description
        assert desc is not None
        assert desc[0][0].upper() == "VAL"
        await cur.fetchone()

    def test_get_schema_table(self, synccon):
        with synccon.cursor() as cur:
            cur.execute("SELECT 1 AS col1, 'x' AS col2")
            rows = cur.get_schema_table()
            assert len(rows) == 2
            assert rows[0]["ColumnName"].upper() == "COL1"
            assert rows[1]["ColumnName"].upper() == "COL2"


class TestSyncRownumber:
    """Test PEP 249 rownumber on sync cursor."""

    TABLE = "test_nzpy_sync_rnumber"

    def test_rownumber_starts_at_zero(self, synccon):
        with synccon.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.rownumber == 0

    def test_rownumber_increments_on_fetch(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {self.TABLE} (id INT)")
            for i in range(5):
                cur.execute(f"INSERT INTO {self.TABLE} VALUES (?)", (i,))
            cur.execute(f"SELECT * FROM {self.TABLE} ORDER BY id")
            assert cur.rownumber == 0
            cur.fetchone()
            assert cur.rownumber == 1
            cur.fetchone()
            assert cur.rownumber == 2
            rest = cur.fetchall()
            assert cur.rownumber == 5
            assert len(rest) == 3
        _drop_table(synccon, self.TABLE)

    def test_rownumber_resets_on_execute(self, synccon):
        with synccon.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
            assert cur.rownumber == 1
            cur.execute("SELECT 2")
            assert cur.rownumber == 0

    def test_cleanup(self, synccon):
        _drop_table(synccon, self.TABLE)


class TestSyncRowcount:
    """Test rowcount property on sync cursor."""

    TABLE = "test_nzpy_sync_rcount"

    def test_rowcount(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {self.TABLE} (id INT)")
            cur.execute(f"INSERT INTO {self.TABLE} VALUES (?)", (1,))
            cur.execute(f"INSERT INTO {self.TABLE} VALUES (?)", (2,))
            cur.execute(f"SELECT * FROM {self.TABLE}")
            rows = cur.fetchall()
            assert len(rows) >= 2
        _drop_table(synccon, self.TABLE)

    def test_cleanup(self, synccon):
        _drop_table(synccon, self.TABLE)


class TestSyncNextset:
    """Test nextset on sync cursor."""

    TABLE = "test_nzpy_sync_ns"

    def test_nextset(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {self.TABLE} (id INT, name VARCHAR(50))")
            cur.execute(f"INSERT INTO {self.TABLE} VALUES (?, ?)", (1, "Alice"))
            cur.execute(f"INSERT INTO {self.TABLE} VALUES (?, ?)", (2, "Bob"))
            cur.execute(f"SELECT id FROM {self.TABLE} ORDER BY id")
            rows = cur.fetchall()
            assert len(rows) >= 2
        _drop_table(synccon, self.TABLE)

    def test_cleanup(self, synccon):
        _drop_table(synccon, self.TABLE)


class TestSyncTimeout:
    """Test timeout parameter on sync cursor execute."""

    TABLE = "test_nzpy_sync_timeout"

    def test_execute_timeout_session_survives(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TEMP TABLE {self.TABLE} (id INT)")
            cur.execute(f"INSERT INTO {self.TABLE} VALUES (1)")

        start = time.monotonic()
        with pytest.raises(nzpy.OperationalError, match="timeout"):
            with synccon.cursor() as cur:
                cur.execute(HEAVY_SQL, timeout=3.0)
                cur.fetchall()
        elapsed = time.monotonic() - start

        # Session must survive — temp table still queryable after timeout
        with synccon.cursor() as cur:
            cur.execute(f"SELECT id FROM {self.TABLE}")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 1

        _drop_table(synccon, self.TABLE)

    def test_cleanup(self, synccon):
        _drop_table(synccon, self.TABLE)

    def test_execute_without_timeout_succeeds(self, synccon):
        with synccon.cursor() as cur:
            cur.execute("SELECT 1", timeout=None)
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 1

    def test_execute_timeout_zero_means_no_limit(self, synccon):
        with synccon.cursor() as cur:
            cur.execute("SELECT 1", timeout=0)
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 1

    def test_connection_timeout_property(self, synccon):
        assert synccon.timeout is None
        synccon.timeout = 30.0
        assert synccon.timeout == 30.0
        synccon.timeout = None
        assert synccon.timeout is None

    def test_connection_timeout_inherited_by_cursor(self, synccon):
        synccon.timeout = 5.0
        c = synccon.cursor()
        assert c.timeout == 5.0
        synccon.timeout = None

    def test_connection_timeout_used_in_execute(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TEMP TABLE {self.TABLE} (id INT)")
            cur.execute(f"INSERT INTO {self.TABLE} VALUES (1)")

        synccon.timeout = 3.0
        start = time.monotonic()
        with pytest.raises(nzpy.OperationalError, match="timeout"):
            with synccon.cursor() as cur:
                cur.execute(HEAVY_SQL)
                cur.fetchall()
        elapsed = time.monotonic() - start

        with synccon.cursor() as cur:
            cur.execute(f"SELECT id FROM {self.TABLE}")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 1

        synccon.timeout = None
        _drop_table(synccon, self.TABLE)

    def test_cursor_timeout_overrides_connection(self, synccon):
        synccon.timeout = 5.0
        c = synccon.cursor()
        c.timeout = 10.0
        assert c.timeout == 10.0
        assert synccon.timeout == 5.0
        c.close()
        synccon.timeout = None

    def test_cursor_timeout_independent_after_creation(self, synccon):
        synccon.timeout = 5.0
        c = synccon.cursor()
        synccon.timeout = 99.0
        assert c.timeout == 5.0
        c.close()
        synccon.timeout = None

    def test_execute_explicit_timeout_overrides_cursor_timeout(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TEMP TABLE {self.TABLE} (id INT)")
            cur.execute(f"INSERT INTO {self.TABLE} VALUES (1)")

        with synccon.cursor() as cur:
            cur.timeout = 30.0
            start = time.monotonic()
            with pytest.raises(nzpy.OperationalError, match="timeout"):
                cur.execute(HEAVY_SQL, timeout=3.0)
                cur.fetchall()
            elapsed = time.monotonic()
            assert elapsed - start < 10.0

        with synccon.cursor() as cur:
            cur.execute(f"SELECT id FROM {self.TABLE}")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 1

        _drop_table(synccon, self.TABLE)


class TestSyncAutocommit:
    """Test autocommit property on SyncConnection."""

    TABLE = "test_nzpy_sync_autocommit"

    def test_autocommit_default_true(self, synccon):
        assert synccon.autocommit is True

    def test_autocommit_setter(self, synccon):
        synccon.autocommit = False
        assert synccon.autocommit is False
        synccon.autocommit = True
        assert synccon.autocommit is True

    def test_autocommit_false_requires_commit(self, synccon):
        _drop_table(synccon, self.TABLE)
        synccon.autocommit = False
        try:
            with synccon.cursor() as cur:
                cur.execute(f"CREATE TABLE {self.TABLE} (id INT)")
                cur.execute(f"INSERT INTO {self.TABLE} VALUES (99)")
            synccon.commit()

            with synccon.cursor() as cur:
                cur.execute(f"SELECT id FROM {self.TABLE}")
                row = cur.fetchone()
                assert row[0] == 99
        finally:
            synccon.autocommit = True
            _drop_table(synccon, self.TABLE)

    def test_autocommit_true_insert_visible_immediately(self, synccon):
        _drop_table(synccon, self.TABLE)
        assert synccon.autocommit is True
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {self.TABLE} (id INT)")
            cur.execute(f"INSERT INTO {self.TABLE} VALUES (42)")
            cur.execute(f"SELECT id FROM {self.TABLE}")
            row = cur.fetchone()
            assert row[0] == 42
        _drop_table(synccon, self.TABLE)

    def test_closed_false_when_open(self, synccon):
        assert synccon.closed is False

    def test_closed_true_after_close(self, db_kwargs_fn):
        conn = sync_nzpy.connect(**db_kwargs_fn)
        assert conn.closed is False
        conn.close()
        assert conn.closed is True

    def test_cleanup(self, synccon):
        synccon.autocommit = True
        _drop_table(synccon, self.TABLE)


class TestSyncCallproc:
    """Test callproc on sync and async cursors."""

    PROC = "test_callproc_echo_py"
    PROC_NOPARAM = "test_callproc_noop_py"

    PROC_SQL = f"""
        CREATE OR REPLACE PROCEDURE {PROC}(INTEGER)
        RETURNS INTEGER
        EXECUTE AS OWNER
        LANGUAGE NZPLSQL AS
        BEGIN_PROC

        BEGIN
            RETURN $1;
        END;

        END_PROC;
    """

    PROC_NOPARAM_SQL = f"""
        CREATE OR REPLACE PROCEDURE {PROC_NOPARAM}()
        RETURNS INTEGER
        EXECUTE AS OWNER
        LANGUAGE NZPLSQL AS
        BEGIN_PROC

        BEGIN
            RETURN 0;
        END;

        END_PROC;
    """

    def _create_proc(self, cur):
        cur.execute(self.PROC_SQL)

    def _create_proc_noparam(self, cur):
        cur.execute(self.PROC_NOPARAM_SQL)

    def _drop_proc(self, cur):
        try:
            cur.execute(f"DROP PROCEDURE {self.PROC}(INTEGER)")
        except Exception:
            pass

    def _drop_proc_noparam(self, cur):
        try:
            cur.execute(f"DROP PROCEDURE {self.PROC_NOPARAM}()")
        except Exception:
            pass

    def test_callproc_no_params_returns_none(self, synccon):
        with synccon.cursor() as cur:
            try:
                self._create_proc_noparam(cur)
            except Exception as e:
                pytest.skip(f"NZPLSQL not available: {e}")

            try:
                result = cur.callproc(self.PROC_NOPARAM)
                assert result is None
            finally:
                self._drop_proc_noparam(cur)

    def test_callproc_params_returns_copy(self, synccon):
        with synccon.cursor() as cur:
            try:
                self._create_proc(cur)
            except Exception as e:
                pytest.skip(f"NZPLSQL not available: {e}")

            try:
                result = cur.callproc(self.PROC, [42])
                assert result == [42]
                assert result is not [42]
            finally:
                self._drop_proc(cur)

    def test_callproc_executes_call_statement_sync(self, synccon):
        with synccon.cursor() as cur:
            try:
                self._create_proc(cur)
            except Exception as e:
                pytest.skip(f"NZPLSQL not available: {e}")

            try:
                result = cur.callproc(self.PROC, [7])
                assert result == [7]
                rows = cur.fetchall()
                assert rows is not None
                assert len(rows) >= 0
            finally:
                self._drop_proc(cur)

    @pytest.mark.asyncio
    async def test_callproc_async(self, con):
        cur = con.cursor()
        try:
            await cur.execute(f"""
                CREATE OR REPLACE PROCEDURE {self.PROC}(INTEGER)
                RETURNS INTEGER
                EXECUTE AS OWNER
                LANGUAGE NZPLSQL AS
                BEGIN_PROC

                BEGIN
                    RETURN $1;
                END;

                END_PROC;
            """)
        except Exception as e:
            pytest.skip(f"NZPLSQL not available: {e}")

        try:
            result = await cur.callproc(self.PROC, [99])
            assert result == [99]
            assert result is not [99]
        finally:
            await cur.execute(f"DROP PROCEDURE {self.PROC}(INTEGER)")

    def test_cleanup(self, synccon):
        with synccon.cursor() as cur:
            self._drop_proc(cur)
            self._drop_proc_noparam(cur)


class TestSyncChaining:
    """Test PEP 249 cursor chaining."""

    TABLE = "test_nzpy_sync_chain"

    def test_execute_returns_self(self, synccon):
        with synccon.cursor() as cur:
            result = cur.execute("SELECT 1")
            assert result is cur

    def test_executemany_returns_self(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {self.TABLE} (id INT, name VARCHAR(50))")
            result = cur.executemany(
                f"INSERT INTO {self.TABLE} VALUES (?, ?)",
                [(1, "a"), (2, "b")]
            )
            assert result is cur
        _drop_table(synccon, self.TABLE)

    def test_execute_fetchone_chain(self, synccon):
        with synccon.cursor() as cur:
            row = cur.execute("SELECT 1").fetchone()
            assert row is not None
            assert row[0] == 1

    def test_execute_fetchall_chain(self, synccon):
        _drop_table(synccon, self.TABLE)
        with synccon.cursor() as cur:
            cur.execute(f"CREATE TABLE {self.TABLE} (id INT)")
            for i in range(3):
                cur.execute(f"INSERT INTO {self.TABLE} VALUES (?)", (i,))
            rows = cur.execute(f"SELECT * FROM {self.TABLE} ORDER BY id").fetchall()
            assert len(rows) == 3
            assert rows[0][0] == 0
            assert rows[2][0] == 2
        _drop_table(synccon, self.TABLE)

    def test_execute_callproc_chain(self, synccon):
        with synccon.cursor() as cur:
            try:
                cur.execute(
                    "CREATE OR REPLACE PROCEDURE test_chain_noop_py() "
                    "RETURNS INTEGER EXECUTE AS OWNER LANGUAGE NZPLSQL AS "
                    "BEGIN_PROC BEGIN RETURN 0; END; END_PROC;"
                )
            except Exception as e:
                pytest.skip(f"NZPLSQL not available: {e}")

            result = cur.callproc("test_chain_noop_py")
            assert result is None

        with synccon.cursor() as cur:
            try:
                cur.execute("DROP PROCEDURE test_chain_noop_py()")
            except Exception:
                pass

    def test_cleanup(self, synccon):
        _drop_table(synccon, self.TABLE)


class TestSyncMessages:
    """Test PEP 249 cursor.messages property."""

    PROC = "test_msg_notice_py"

    def test_messages_exists(self, synccon):
        with synccon.cursor() as cur:
            msgs = cur.messages
            assert msgs is not None

    def test_messages_is_notices(self, synccon):
        with synccon.cursor() as cur:
            assert cur.messages is cur._c.notices

    def test_notice_in_messages(self, synccon):
        with synccon.cursor() as cur:
            try:
                cur.execute(f"""
                    CREATE OR REPLACE PROCEDURE {self.PROC}()
                    RETURNS INTEGER
                    EXECUTE AS OWNER
                    LANGUAGE NZPLSQL AS
                    BEGIN_PROC

                    BEGIN
                        RAISE NOTICE 'hello from callproc';
                        RETURN 0;
                    END;

                    END_PROC;
                """)
            except Exception as e:
                pytest.skip(f"NZPLSQL not available: {e}")

            cur.callproc(self.PROC)
            found = any("hello from callproc" in str(m) for m in cur.messages)
            assert found, f"Expected notice 'hello from callproc' in messages: {list(cur.messages)}"

        with synccon.cursor() as cur:
            try:
                cur.execute(f"DROP PROCEDURE {self.PROC}()")
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_messages_async(self, con):
        cur = con.cursor()
        assert cur.messages is cur.notices
        await cur.execute("SELECT 1")
        await cur.fetchone()

    def test_cleanup(self, synccon):
        with synccon.cursor() as cur:
            try:
                cur.execute(f"DROP PROCEDURE {self.PROC}()")
            except Exception:
                pass


class TestSyncLoadData:
    """Test load_data on sync connection."""

    TABLE = "test_nzpy_sync_loaddata"

    def test_load_data_auto_create(self, synccon):
        _drop_table(synccon, self.TABLE)
        rows = [(1, "Alice"), (2, "Bob"), (3, "Charlie")]
        count = synccon.load_data(self.TABLE, rows)
        assert count == 3

        with synccon.cursor() as cur:
            cur.execute(f"SELECT * FROM {self.TABLE} ORDER BY 1")
            result = cur.fetchall()
            assert len(result) == 3
            assert result[0][0] == 1
        _drop_table(synccon, self.TABLE)

    def test_load_data_with_columns(self, synccon):
        _drop_table(synccon, self.TABLE)
        columns = [("id", "INTEGER"), ("name", "VARCHAR(50)")]
        rows = [(1, "Alice"), (2, "Bob")]
        count = synccon.load_data(self.TABLE, rows, columns=columns)
        assert count == 2

        with synccon.cursor() as cur:
            cur.execute(f"SELECT * FROM {self.TABLE} ORDER BY id")
            result = cur.fetchall()
            assert result[0][1] == "Alice"
        _drop_table(synccon, self.TABLE)

    def test_standalone_load_data_function(self, synccon):
        _drop_table(synccon, self.TABLE)
        rows = [(42,)]
        count = sync_nzpy.load_data(synccon, self.TABLE, rows)
        assert count == 1
        _drop_table(synccon, self.TABLE)

    def test_cleanup(self, synccon):
        _drop_table(synccon, self.TABLE)
