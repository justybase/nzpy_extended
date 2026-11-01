"""A value that does not decode must not make the batch decoder rescan the result.

The native batch decoder stops in front of the first row whose values do not
decode and leaves that row unconsumed. The reader then decodes that row alone,
which defers the error, and continues with the batch. Each row is decoded once,
so one bad value costs one extra call rather than a rescan of the whole buffer.
"""

from __future__ import annotations

import socket
import struct
from types import SimpleNamespace
from typing import Any

import pytest

from nzpy_extended import _native
from nzpy_extended import _protocol
from nzpy_extended._dbos import DbosParser
from nzpy_extended.buffered_stream import NzBufferedStream
from nzpy_extended.protocol import NzTypeInt, NzTypeVarChar
from nzpy_extended.types import DbosTupleDesc

pytestmark = pytest.mark.unit

needs_native = pytest.mark.skipif(
    not _native._HAVE_NATIVE, reason="native extension is not built"
)

ROWS = 3000
BAD_ROW = 1500  # its VARCHAR is not valid UTF-8


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


def _row_payload(number: int, text: bytes = b"abc") -> bytes:
    varying_length = len(text) + 2
    varying = struct.pack("<H", varying_length) + text
    if varying_length % 2:
        varying += b"\x00"
    return b"\x00\x00" + bytes([0]) + struct.pack("<i", number) + varying


def _dbos_frame(payload: bytes) -> bytes:
    return b"Y" + b"\x00\x00\x00\x01" + b"\x00" * 4 + struct.pack(">i", len(payload)) + payload


def _stream_over(blob: bytes) -> tuple[NzBufferedStream, socket.socket, socket.socket]:
    left, right = socket.socketpair()
    size = len(blob) + 64  # keep clear of the shared pool size
    stream = NzBufferedStream(left, max_size=size, buffer_size=size)
    assert stream.view is not None
    stream.view[: len(blob)] = blob
    stream.tail = len(blob)
    return stream, left, right


class _CountingNative:
    """Delegates to the native module and counts batch calls."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.batch_calls = 0

    def process_dbos_batch(self, *args: Any) -> Any:
        self.batch_calls += 1
        return self._real.process_dbos_batch(*args)

    def process_dbos_row(self, *args: Any) -> Any:
        return self._real.process_dbos_row(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@needs_native
@pytest.mark.asyncio
async def test_reader_decodes_each_row_once_around_a_bad_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counting = _CountingNative(_native._native_ext)
    monkeypatch.setattr(_native, "_native_ext", counting)
    blob = b"".join(
        _dbos_frame(_row_payload(i, b"\xff\xfe" if i == BAD_ROW else b"abc"))
        for i in range(ROWS)
    )
    blob += b"Z" + struct.pack(">i", 5) + b"I"
    stream, left, right = _stream_over(blob)
    deferred: list[Exception] = []
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
        conn._defer_decode_error = deferred.append  # type: ignore[attr-defined]
        proto = _protocol.ProtocolHandler.__new__(_protocol.ProtocolHandler)
        proto._conn = conn  # type: ignore[attr-defined]
        cursor = SimpleNamespace(ps={"tupdesc": desc, "row_desc": []}, cached_rows=[], has_rows=False)

        states: list[str] = []
        async for state in proto._read_result_frames(cursor):  # type: ignore[arg-type]
            states.append(state)
            if state == "READY_FOR_QUERY":
                break

        assert states[-1] == "READY_FOR_QUERY"
        assert len(cursor.cached_rows) == ROWS - 1
        assert len(deferred) == 1
        assert isinstance(deferred[0], Exception)
        # A rescan would make about BAD_ROW calls; one stop plus one resume is enough.
        assert counting.batch_calls <= 4, f"batch decoder called {counting.batch_calls} times"
    finally:
        stream.close()
        left.close()
        right.close()
