"""Live regressions using session-local objects only."""
import uuid
from decimal import Decimal

import pytest
import nzpy_extended as nz
from nzpy_extended import sync

pytestmark = pytest.mark.full


def test_transaction_context_commits_and_rolls_back(db_kwargs_fn):
    conn = sync.connect(**db_kwargs_fn, connect_timeout=5)
    name = 'NZPY_CONTRACT_' + uuid.uuid4().hex[:12].upper()
    try:
        conn.execute(f'CREATE TEMP TABLE {name} (id INTEGER)')
        with conn.transaction():
            conn.execute(f'INSERT INTO {name} VALUES (1)')
        assert conn.autocommit is True
        with pytest.raises(ValueError, match='rollback'):
            with conn.transaction():
                conn.execute(f'INSERT INTO {name} VALUES (2)')
                raise ValueError('rollback')
        assert conn.autocommit is True
        assert conn.execute(f'SELECT id FROM {name} ORDER BY id').fetchall() == [[1]]
        with conn.transaction():
            with pytest.raises(nz.NotSupportedError):
                with conn.transaction():
                    pass
    finally:
        conn.close()


def test_live_type_categories_and_recovery(db_kwargs_fn):
    conn = sync.connect(**db_kwargs_fn, connect_timeout=5)
    try:
        cur = conn.cursor()
        for sql, value, category in [
            ('SELECT CAST(? AS BIGINT)', 9223372036854775807, nz.NUMBER),
            ('SELECT CAST(? AS NUMERIC(38,10))', Decimal('12345678901234567890.1234567890'), nz.NUMBER),
            ('SELECT CAST(? AS VARCHAR(30))', ' ab ', nz.STRING),
        ]:
            cur.execute(sql, (value,), timeout=5)
            assert cur.fetchone() == [value]
            assert cur.description[0][1] == category
        with pytest.raises(nz.ProgrammingError):
            cur.execute('SELECT 1 +', timeout=5)
        cur.execute('SELECT 7', timeout=5)
        assert cur.fetchone() == [7]
        cur.close()
        with pytest.raises(nz.InterfaceError):
            cur.fetchone()
    finally:
        conn.close()
