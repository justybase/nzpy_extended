"""The result reader must give the event loop turns while a result streams.

When the server outruns the decoder, every read is served from buffered bytes
and never suspends. Without an explicit yield, timers and cancel() on the same
loop wait until the whole result has been decoded, so a cancel sent after a
short sleep only takes effect at the end of the result.
"""

from __future__ import annotations

import asyncio
import socket
import struct
from types import SimpleNamespace

import pytest

from nzpy_extended import _native
from nzpy_extended import _protocol
from nzpy_extended._dbos import DbosParser
from nzpy_extended.buffered_stream import NzBufferedStream
from nzpy_extended.protocol import NzTypeInt, NzTypeVarChar
from nzpy_extended.types import DbosTupleDesc

pytestmark = pytest.mark.unit

# Large enough to span several 5 ms loop-yield intervals even when the
# repeated text column is served from the value cache.
ROWS = 100_000


def _descriptor() -> DbosTupleDesc:
    desc = DbosTupleDesc()
    desc.nullsAllowed = 1
    desc.numFields = 2
    desc.numVaryingFields = 1
    desc.fixedFieldsSize = 7
    desc.field_type = [NzTypeInt, NzTypeVarChar]
    desc.field_size = [4, 32]
    desc.field_trueSize = [4, 32]
    desc.field_offset = [3, 0]
    desc.field_fixedSize = [4, 0]
    desc.field_physField = [0, 1]
    return desc


def _row_payload(number: int) -> bytes:
    encoded = b"abc"
    varying_length = len(encoded) + 2
    varying = struct.pack("<H", varying_length) + encoded
    if varying_length % 2:
        varying += b"\x00"  # varying fields are padded to an even length
    return b"\x00\x00" + bytes([0]) + struct.pack("<i", number) + varying


def _dbos_frame(payload: bytes) -> bytes:
    return b"Y" + b"\x00\x00\x00\x01" + b"\x00" * 4 + struct.pack(">i", len(payload)) + payload


def _result_blob() -> bytes:
    frames = b"".join(_dbos_frame(_row_payload(i)) for i in range(ROWS))
    return frames + b"Z" + struct.pack(">i", 5) + b"I"


def _stream_over(blob: bytes) -> tuple[NzBufferedStream, socket.socket, socket.socket]:
    left, right = socket.socketpair()
    size = len(blob) + 64  # keep clear of the shared pool size
    stream = NzBufferedStream(left, max_size=size, buffer_size=size)
    assert stream.view is not None
    stream.view[: len(blob)] = blob
    stream.tail = len(blob)
    return stream, left, right


@pytest.mark.asyncio
async def test_reader_yields_to_event_loop_while_result_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_native, "_HAVE_NATIVE", False)
    stream, left, right = _stream_over(_result_blob())
    try:
        desc = _descriptor()
        conn = SimpleNamespace(
            _stream=stream,
            _cached_header=None,
            _frame_in_progress=False,
            _char_varchar_encoding="utf-8",
            _client_encoding="utf-8",
            tupdesc=desc,
            _dirty_socket=True,
            _active_generator=object(),
            _active_cursor=object(),
            log=SimpleNamespace(debug=lambda *a, **k: None, warning=lambda *a, **k: None),
        )

        async def _read(n: int) -> bytes:
            return await stream.read(n)

        conn._read = _read  # type: ignore[attr-defined]
        conn._dbos = DbosParser(conn)  # type: ignore[arg-type]
        conn._defer_decode_error = lambda exc: None  # type: ignore[attr-defined]
        proto = _protocol.ProtocolHandler.__new__(_protocol.ProtocolHandler)
        proto._conn = conn  # type: ignore[attr-defined]
        cursor = SimpleNamespace(ps={"tupdesc": desc, "row_desc": []}, cached_rows=[], has_rows=False)

        ticks_while_reading = 0
        finished = False

        async def ticker() -> None:
            nonlocal ticks_while_reading
            while not finished:
                await asyncio.sleep(0.0005)
                if not finished:
                    ticks_while_reading += 1

        ticking = asyncio.create_task(ticker())
        await asyncio.sleep(0)  # let the ticker start
        states: list[str] = []
        async for state in proto._read_result_frames(cursor):  # type: ignore[arg-type]
            states.append(state)
            if state == "READY_FOR_QUERY":
                break
        finished = True
        await ticking

        assert len(cursor.cached_rows) == ROWS
        assert states[-1] == "READY_FOR_QUERY"
        assert ticks_while_reading >= 3, (
            "event loop got no turns while the result streamed; "
            f"timer ticks during read = {ticks_while_reading}"
        )
    finally:
        stream.close()
        left.close()
        right.close()
