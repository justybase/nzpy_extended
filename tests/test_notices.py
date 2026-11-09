"""
test_notices.py
===============
Python equivalent of NoticeTests.cs from JustyBase.NetezzaDriver.Tests.

The nzpy_extended cursor collects notices via cursor.notices and
cursor.notice_handler. This file tests both mechanisms, mirroring the
C# NoticeReceived event pattern.
"""

import os
import pytest

import nzpy_extended as nzpy

pytestmark = pytest.mark.full


NZ_HOST     = os.environ.get("NZ_DEV_HOST",     "127.0.0.1")
NZ_PORT     = int(os.environ.get("NZ_DEV_PORT",  "5480"))
NZ_DB       = os.environ.get("NZ_DEV_DB",        "JUST_DATA")
NZ_USER     = os.environ.get("NZ_DEV_USER",      "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD",  "password")

CONN_KWARGS = dict(
    user=NZ_USER, password=NZ_PASSWORD,
    host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
)

PROC_NAME = "JUST_DATA.ADMIN.CUSTOMER_PY_NOTICE_TEST"

CREATE_PROC_SQL = f"""
CREATE OR REPLACE PROCEDURE {PROC_NAME}()
RETURNS INTEGER EXECUTE AS OWNER LANGUAGE NZPLSQL AS
BEGIN_PROC
    BEGIN
        RAISE NOTICE 'The customer name is alpha';
        RAISE NOTICE 'The customer location is beta';
    END;
END_PROC
"""


async def _make_conn():
    return await nzpy.connect(**CONN_KWARGS)


async def _ensure_procedure():
    """Create (or replace) the test stored procedure."""
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        await cur.execute(CREATE_PROC_SQL)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Basic notice test – mirrors BasicNoticeTests in C#
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_basic_notice_via_handler():
    """
    Mirrors BasicNoticeTests.

    Calls the stored procedure and verifies that both RAISE NOTICE messages
    are captured by the notice_handler callback.
    """
    await _ensure_procedure()
    conn = await _make_conn()
    try:
        cur = conn.cursor()

        collected_notices = []

        def handler(notice):
            collected_notices.append(notice)

        cur.notice_handler = handler

        await cur.execute(f"CALL {PROC_NAME}()")

        assert len(collected_notices) == 2, (
            f"Expected 2 notices, got {len(collected_notices)}: {collected_notices}"
        )
        assert "The customer name is alpha" in collected_notices[0], (
            f"First notice mismatch: {collected_notices[0]!r}"
        )
        assert "The customer location is beta" in collected_notices[1], (
            f"Second notice mismatch: {collected_notices[1]!r}"
        )

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_basic_notice_via_cursor_notices():
    """
    Verifies that cursor.notices (built-in deque) also contains both notices.
    """
    await _ensure_procedure()
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        await cur.execute(f"CALL {PROC_NAME}()")

        notices = list(cur.notices)
        assert len(notices) == 2, (
            f"Expected 2 notices in cursor.notices, got {len(notices)}: {notices}"
        )
        assert "The customer name is alpha" in notices[0], (
            f"First notice mismatch: {notices[0]!r}"
        )
        assert "The customer location is beta" in notices[1], (
            f"Second notice mismatch: {notices[1]!r}"
        )

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_notice_handler_and_deque_agree():
    """Both the handler and cursor.notices must agree on notice content."""
    await _ensure_procedure()
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        handler_notices = []
        cur.notice_handler = lambda n: handler_notices.append(n)

        await cur.execute(f"CALL {PROC_NAME}()")

        deque_notices = list(cur.notices)

        assert len(handler_notices) == len(deque_notices) == 2
        for h, d in zip(handler_notices, deque_notices):
            assert h == d, f"Handler and deque disagree: handler={h!r}, deque={d!r}"

    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_no_notices_on_plain_query():
    """A plain SELECT must not produce any notices."""
    conn = await _make_conn()
    try:
        cur = conn.cursor()
        notices = []
        cur.notice_handler = lambda n: notices.append(n)

        await cur.execute("SELECT 1")
        await cur.fetchall()

        assert notices == [], f"Expected no notices, got: {notices}"
        assert len(cur.notices) == 0, f"Expected empty cursor.notices, got: {list(cur.notices)}"

    finally:
        await conn.close()
