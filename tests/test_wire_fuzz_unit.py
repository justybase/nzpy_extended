"""Seeded fuzz and property tests for the wire protocol (no database).

Every generator takes an explicit seed, so a failing case reproduces exactly.
The properties checked are:

* a damaged response only ever surfaces as a driver error (``Error``), never
  as a bare Python exception, and never hangs the client;
* the same response delivered in any fragmentation decodes identically;
* the native and pure-Python DBOS decoders agree on random row payloads.
"""

from __future__ import annotations

import asyncio
import random
import socket
import struct
from pathlib import Path
from typing import Any

import pytest

import nzpy_extended as nz
from nzpy_extended import _native
from nzpy_extended.exceptions import DataError, Error, InterfaceError
from tests.mock_netezza import (
    MockNetezzaServer,
    ServerSession,
    default_responder,
    is_connect_setup,
    message,
    read_wire_records,
)
from tests.test_dbos_malformed_unit import _decode_c, _decode_python, _row, _varying
from tests.test_wire_replay_unit import Outcome, replay

pytestmark = pytest.mark.unit

SEED_DIR = Path(__file__).parent / "data" / "fuzz_seeds"
TIMEOUT = 10.0

# Fixture records whose responses are worth damaging: text, DBOS, NULLs, notices, errors.
FUZZ_TARGETS: dict[str, int] = {
    "text_types": 0,
    "dbos_types": 3,
    "null_matrix": 3,
    "multi_result": 0,
    "notice": 0,
    "error": 0,
}


def mutate(rng: random.Random, data: bytes) -> bytes:
    """One random structural damage: flips, truncation, insertion, length overwrite, repeat."""
    out = bytearray(data)
    choice = rng.randrange(5)
    if choice == 0 and out:
        for _ in range(rng.randint(1, 4)):
            out[rng.randrange(len(out))] = rng.randrange(256)
    elif choice == 1 and len(out) > 1:
        del out[rng.randrange(1, len(out)):]
    elif choice == 2:
        at = rng.randrange(len(out) + 1)
        out[at:at] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 8)))
    elif choice == 3 and len(out) >= 4:
        at = rng.randrange(len(out) - 3)
        value = rng.choice([-1, 0, 1, 2**31 - 1, -2**31, rng.randrange(2**16)])
        out[at:at + 4] = struct.pack(">i", value)
    elif out:
        start = rng.randrange(len(out))
        end = rng.randrange(start, len(out)) + 1
        at = rng.randrange(len(out) + 1)
        out[at:at] = out[start:end]
    return bytes(out)


def _serve_once(sql: str, response: bytes) -> Any:
    """Answer the single user statement with ``response``, then close the socket."""

    def respond(statement: str, session: ServerSession) -> None:
        if is_connect_setup(statement):
            default_responder(statement, session)
            return
        if statement != sql:
            raise AssertionError(f"unexpected statement {statement!r}")
        session.send(response)
        session.sock.shutdown(socket.SHUT_RDWR)

    return respond


async def _run_single(sql: str, response: bytes) -> Error | None:
    """Return the driver error raised by the damaged response, or None on success.

    Anything that is not a driver ``Error`` propagates and fails the test, and a
    hang fails it through the timeout.
    """
    server = MockNetezzaServer(responder=_serve_once(sql, response))
    server.start()
    conn = None
    try:
        conn = await asyncio.wait_for(nz.connect(**server.connect_kwargs()), TIMEOUT)
        cursor = conn.cursor()
        await asyncio.wait_for(cursor.execute(sql), TIMEOUT)
        if cursor.ps and cursor.ps.get("row_desc"):
            await asyncio.wait_for(cursor.fetchall(), TIMEOUT)
            while await asyncio.wait_for(cursor.nextset(), TIMEOUT):
                await asyncio.wait_for(cursor.fetchall(), TIMEOUT)
        return None
    except Error as exc:
        return exc
    except asyncio.TimeoutError:
        pytest.fail(f"client hung on a damaged response for {sql!r}")
    finally:
        if conn is not None:
            await conn.close()
        server.close()


def _fixture_response(name: str, index: int) -> tuple[str, bytes]:
    sql, response = read_wire_records(name)[index]
    return sql, response


@pytest.mark.asyncio
@pytest.mark.timeout(900)
@pytest.mark.parametrize("name", sorted(FUZZ_TARGETS))
async def test_mutated_responses_only_raise_driver_errors(name: str) -> None:
    sql, original = _fixture_response(name, FUZZ_TARGETS[name])
    for case in range(40):
        rng = random.Random(f"{name}:{case}")
        damaged = mutate(rng, original)
        await _run_single(sql, damaged)


@pytest.mark.asyncio
@pytest.mark.timeout(300)
@pytest.mark.parametrize("seed_file", sorted(p.name for p in SEED_DIR.glob("*.bin")))
async def test_protocol_seeds_fail_cleanly_as_raw_and_framed_responses(seed_file: str) -> None:
    seed = (SEED_DIR / seed_file).read_bytes()
    sql = "SELECT 1 AS SEED"
    await _run_single(sql, seed)
    await _run_single(sql, message(b"T", seed))


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_fragmentation_is_invisible_to_results_for_random_chunk_sizes() -> None:
    reference = await replay("null_matrix", None)
    for case in range(12):
        chunk = random.Random(f"chunk:{case}").randint(1, 64)
        outcomes = await replay("null_matrix", chunk)
        assert _outcome_values(outcomes) == _outcome_values(reference), f"chunk={chunk}"


def _outcome_values(outcomes: list[Outcome]) -> list[Any]:
    return [(o.result_sets, o.raw_rows, o.rowcount, o.error is None) for o in outcomes]


@pytest.mark.skipif(not _native._HAVE_NATIVE, reason="native extension is not built")
def test_random_dbos_payloads_decode_identically_in_c_and_python() -> None:
    texts = [b"", b"a", b"abc\xc3\x9f", b"\xff\xfe", b"x" * 40]
    checked = 0
    for case in range(400):
        rng = random.Random(f"dbos:{case}")
        number = rng.randrange(-2**31, 2**31)
        text = rng.choice(texts)
        bitmap = rng.choice([0, 0x02, 0x80])
        payload = bytearray(_row(number, _varying(text), bitmap))
        if rng.random() < 0.5:
            mutated = mutate(rng, bytes(payload))
            payload = bytearray(mutated)
        payload_bytes = bytes(payload)

        c_result: Any = None
        native_error: BaseException | None = None
        try:
            c_result = _decode_c(payload_bytes)
        except BaseException as exc:  # noqa: BLE001  (categories are compared below)
            native_error = exc
        py_result: Any = None
        py_error: BaseException | None = None
        try:
            py_result = _decode_python(payload_bytes)
        except BaseException as exc:  # noqa: BLE001
            py_error = exc

        if native_error is None and py_error is None:
            assert c_result == py_result[0], f"case {case}: value mismatch"
        elif isinstance(native_error, BufferError):
            assert isinstance(py_error, InterfaceError), f"case {case}: {py_error!r}"
        elif isinstance(native_error, UnicodeDecodeError):
            assert isinstance(py_error, DataError), f"case {case}: {py_error!r}"
        else:
            pytest.fail(f"case {case}: native={native_error!r} Python={py_error!r} payload={payload_bytes!r}")
        checked += 1
    assert checked == 400
