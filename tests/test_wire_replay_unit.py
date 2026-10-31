"""Replay of golden wire fixtures captured from a real appliance (no database).

The fixtures in ``tests/data/wire`` hold byte-exact server responses to
synthetic statements (see their README). Each is served record by record by
:class:`MockNetezzaServer`, and the decoded results are compared with the
expectations the Rust driver's ``tests/wire_replay.rs`` states for the same
records. Every response is also replayed under several fragmentation sizes,
and truncated at many cut points, which must each fail with a driver error.
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import pytest

import nzpy_extended as nz
from nzpy_extended.exceptions import Error
from tests.mock_netezza import (
    PADDING,
    MockNetezzaServer,
    Responder,
    ServerSession,
    default_responder,
    is_connect_setup,
    read_wire_records,
    replay_responder,
)

pytestmark = pytest.mark.unit

STATEMENT_TIMEOUT = 10.0


@dataclass
class Outcome:
    result_sets: list[list[list[str]]] = field(default_factory=list)
    raw_rows: list[list[Any]] = field(default_factory=list)
    rowcount: int = -1
    notices: list[str] = field(default_factory=list)
    error: BaseException | None = None


def display(value: Any) -> str:
    """Render a decoded value the way the Rust expectations spell it."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


async def _collect(cursor: nz.Cursor) -> Outcome:
    outcome = Outcome()
    has_result = bool(cursor.ps and cursor.ps.get("row_desc"))
    if has_result:
        rows = await cursor.fetchall()
        outcome.raw_rows.append(rows)
        outcome.result_sets.append([[display(v) for v in row] for row in rows])
        while await cursor.nextset():
            rows = await cursor.fetchall()
            outcome.raw_rows.append(rows)
            outcome.result_sets.append([[display(v) for v in row] for row in rows])
    outcome.rowcount = cursor.rowcount
    outcome.notices = [str(n) for n in cursor.notices]
    return outcome


async def replay(name: str, chunk: int | None = None) -> list[Outcome]:
    """Run every record of a fixture on one session and collect each outcome."""
    records = read_wire_records(name)
    server = MockNetezzaServer(responder=replay_responder(records), chunk=chunk)
    server.start()
    outcomes: list[Outcome] = []
    conn = None
    try:
        conn = await asyncio.wait_for(nz.connect(**server.connect_kwargs()), STATEMENT_TIMEOUT)
        for sql, _ in records:
            cursor = conn.cursor()
            try:
                await asyncio.wait_for(cursor.execute(sql), STATEMENT_TIMEOUT)
                outcomes.append(await asyncio.wait_for(_collect(cursor), STATEMENT_TIMEOUT))
            except Error as exc:
                outcomes.append(Outcome(error=exc))
        server.assert_no_handler_errors()
    finally:
        if conn is not None:
            await conn.close()
        server.close()
    return outcomes


def _rows(outcome: Outcome, result_set: int = 0) -> list[list[str]]:
    assert outcome.error is None, f"unexpected error: {outcome.error!r}"
    return outcome.result_sets[result_set]


def _strs(*values: str) -> list[str]:
    return list(values)


CHUNKS = [None, 1, 3, 7]


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", CHUNKS)
async def test_text_types(chunk: int | None) -> None:
    (outcome,) = await replay("text_types", chunk)
    assert _rows(outcome) == [_strs(
        "-2147483648",
        "Zażółć gęślą jaźń",
        "3.1400",
        "2024-02-29",
        "2024-02-29 12:34:56.123456",
        "true",
        "1.5",
        "12:34:56",
    )]


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", CHUNKS)
async def test_dbos_types(chunk: int | None) -> None:
    outcomes = await replay("dbos_types", chunk)
    assert all(o.error is None for o in outcomes[:3])
    assert outcomes[1].rowcount == 1
    assert _rows(outcomes[3]) == [
        _strs(
            "-2147483648",
            "Zażółć gęślą jaźń",
            "3.1400",
            "2024-02-29",
            "2024-02-29 12:34:56.123456",
            "true",
            "1.5",
            "12:34:56",
            "-9223372036854775807",
            "-32768",
            "-128",
        ),
        _strs(
            "2147483647",
            "",
            "-0.0001",
            "0001-01-01",
            "1999-12-31 23:59:59.999999",
            "false",
            "-2.5",
            "00:00:00",
            "9223372036854775807",
            "32767",
            "127",
        ),
    ]
    # The empty VARCHAR is an empty string, not NULL.
    assert outcomes[3].raw_rows[0][1][1] == ""
    assert outcomes[3].raw_rows[0][1][8] == 9223372036854775807


def _null_matrix_expected() -> list[str]:
    return [
        "NULL" if i % 2 == 0 else (f"v{i}" if i % 3 == 2 else f"{i}")
        for i in range(33)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", CHUNKS)
async def test_null_matrix_text_and_binary_paths(chunk: int | None) -> None:
    outcomes = await replay("null_matrix", chunk)
    expected = _null_matrix_expected()
    assert _rows(outcomes[0]) == [expected]
    assert _rows(outcomes[3]) == [expected]
    for index, value in enumerate(outcomes[3].raw_rows[0][0]):
        assert (value is None) == (index % 2 == 0), f"column {index}"


NUMERIC_CASES: list[tuple[int, list[str]]] = [
    (0, ["0", "9", "-9"]),
    (0, ["0", "999999999999999999", "-999999999999999999"]),
    (0, ["0", "99999999999999999999999999999999999999"]),
    (4, ["0", "3.1400", "-0.0001", "999999.9999"]),
    (10, ["1.0000000001", "-12345678901234567890123456.7890123456"]),
    (38, ["0.99999999999999999999999999999999999999", "-0.5"]),
]


def _at_scale(value: str, scale: int) -> str:
    negative = value.startswith("-")
    digits = value.lstrip("-")
    int_part, _, frac = digits.partition(".")
    frac = frac.ljust(scale, "0")
    zero = int_part.strip("0") == "" and frac.strip("0") == ""
    sign = "-" if negative and not zero else ""
    return f"{sign}{int_part}" if scale == 0 else f"{sign}{int_part}.{frac}"


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", [None, 7])
async def test_numeric_matrix_exact_on_binary_path(chunk: int | None) -> None:
    outcomes = await replay("numeric_matrix", chunk)
    statement = 0
    for scale, values in NUMERIC_CASES:
        statement += 1 + len(values)  # CREATE + INSERTs
        expected = [_at_scale(v, scale) for v in values]

        binary = outcomes[statement]
        assert binary.error is None
        assert [row[0] for row in _rows(binary)] == expected

        text = outcomes[statement + 1]
        assert len(_rows(text)) == 1
        for index, want in enumerate(expected):
            got = text.raw_rows[0][0][index]
            if isinstance(got, float):
                assert got == float(want)
            else:
                assert display(got) == want
        statement += 2
    assert statement == len(outcomes)


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", [None, 4096])
async def test_varchar_32767_boundaries(chunk: int | None) -> None:
    outcomes = await replay("varchar_32767", chunk)

    def check_long(value: str, length: int) -> None:
        assert len(value) == length
        assert value.startswith("A") and value.endswith("Z")
        assert set(value[1:-1]) <= {"x"}

    for index, length in zip((2, 4, 6), (32766, 32767, 32768)):
        check_long(_rows(outcomes[index])[0][0], length)

    table = _rows(outcomes[-1])
    assert [row[0] for row in table] == ["32766", "32767", "32768"]
    for row, length in zip(table, (32766, 32767, 32768)):
        check_long(row[1], length)


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", CHUNKS)
async def test_multiple_result_sets(chunk: int | None) -> None:
    (outcome,) = await replay("multi_result", chunk)
    assert outcome.result_sets == [[["1"]], [["x", "2"]], [["NULL"]]]


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", CHUNKS)
async def test_notices_are_delivered_and_do_not_disturb_results(chunk: int | None) -> None:
    outcomes = await replay("notice", chunk)
    assert any("ROLLBACK: no transaction in progress" in n for n in outcomes[0].notices)
    assert any("COMMIT: no transaction in progress" in n for n in outcomes[1].notices)
    assert outcomes[2].notices == []
    assert _rows(outcomes[3]) == [["1"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", CHUNKS)
async def test_errors_then_success_on_the_same_session(chunk: int | None) -> None:
    outcomes = await replay("error", chunk)
    assert isinstance(outcomes[0].error, Error)
    assert "found a keyword" in str(outcomes[0].error)
    assert isinstance(outcomes[1].error, Error)
    assert "Divide by 0" in str(outcomes[1].error)
    assert _rows(outcomes[2]) == [["1"]]


# ---------------------------------------------------------------------------
# Truncation: every cut point must fail cleanly, never hang and never succeed.
# ---------------------------------------------------------------------------


def _cut_points(length: int) -> list[int]:
    """Every early cut (message headers), then about sixteen evenly spaced ones."""
    step = max(1, length // 16)
    return sorted(set(range(1, min(12, length))) | set(range(12, length, step)))


def _truncating_responder(
    records: list[tuple[str, bytes]], index: int, cut: int
) -> Responder:
    """Serve records in order; the record at ``index`` stops after ``cut`` bytes."""
    position = 0

    def respond(sql: str, session: ServerSession) -> None:
        nonlocal position
        if is_connect_setup(sql):
            default_responder(sql, session)
            return
        expected_sql, response = records[position]
        position += 1
        if sql != expected_sql:
            raise AssertionError(f"statement {sql!r} does not match {expected_sql!r}")
        if position - 1 < index:
            session.send(response + PADDING)
            return
        session.send(response[:cut])
        session.sock.shutdown(socket.SHUT_RDWR)

    return respond


@pytest.mark.asyncio
@pytest.mark.timeout(600)
@pytest.mark.parametrize("name", ["text_types", "dbos_types", "null_matrix", "multi_result", "notice", "error"])
async def test_truncated_responses_fail_cleanly(name: str) -> None:
    records = read_wire_records(name)
    for index, (_, response) in enumerate(records):
        for cut in _cut_points(len(response)):
            served = records[:index + 1]
            server = MockNetezzaServer(responder=_truncating_responder(served, index, cut))
            server.start()
            conn = None
            try:
                conn = await asyncio.wait_for(nz.connect(**server.connect_kwargs()), STATEMENT_TIMEOUT)
                failed = False
                for position, (sql, _) in enumerate(served):
                    cursor = conn.cursor()
                    try:
                        await asyncio.wait_for(cursor.execute(sql), STATEMENT_TIMEOUT)
                        await asyncio.wait_for(_collect(cursor), STATEMENT_TIMEOUT)
                    except Error:
                        # Earlier fixture records may legitimately be server errors.
                        if position == index:
                            failed = True
                            break
                    except asyncio.TimeoutError:
                        pytest.fail(f"{name}[{index}] cut {cut}: client hung")
                assert failed, f"{name}[{index}] cut {cut}: truncated response was accepted"
            finally:
                if conn is not None:
                    await conn.close()
                server.close()
