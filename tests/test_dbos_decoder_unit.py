"""Focused unit coverage for DBOS row views and batched decoding."""

from __future__ import annotations

import logging
import struct
from collections import deque
from types import SimpleNamespace

import pytest

from nzpy_extended import _cstate
from nzpy_extended._dbos import DbosParser
from nzpy_extended.core import Connection
from nzpy_extended.types import DbosTupleDesc
from nzpy_extended.protocol import NzTypeInt, NzTypeVarChar

pytestmark = pytest.mark.unit


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


def _row_payload(number: int, text: str | None, *, null_text: bool = False) -> bytes:
    bitmap = 0x02 if null_text else 0
    encoded = b"" if text is None else text.encode("utf-8")
    varying_length = len(encoded) + 2
    varying = struct.pack("<H", varying_length) + encoded
    if varying_length % 2:
        varying += b"\x00"
    return b"\x00\x00" + bytes([bitmap]) + struct.pack("<i", number) + varying


def _dbos_frame(payload: bytes) -> bytes:
    # Y, shared message header, reserved word, DBOS row length, row payload.
    return b"Y" + b"\x00\x00\x00\x01" + b"\x00" * 4 + struct.pack(">i", len(payload)) + payload


def _parser() -> DbosParser:
    conn = SimpleNamespace(
        _char_varchar_encoding="utf-8",
        _client_encoding="utf-8",
    )
    return DbosParser(conn)  # type: ignore[arg-type]


def test_dbos_python_fallback_decodes_memoryview_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_cstate, "_HAVE_C_EXT", False)
    cursor = SimpleNamespace(cached_rows=[])
    payload = memoryview(_row_payload(42, "café"))

    _parser()._process_dbos_payload(cursor, _descriptor(), payload)  # type: ignore[arg-type]

    assert cursor.cached_rows == [[42, "café"]]


@pytest.mark.skipif(not _cstate._HAVE_C_EXT, reason="C extension is not built")
def test_dbos_c_row_decoder_accepts_memoryview_payload() -> None:
    cursor = SimpleNamespace(cached_rows=[])
    payload = memoryview(_row_payload(-7, "wide λ"))

    _parser()._process_dbos_payload(cursor, _descriptor(), payload)  # type: ignore[arg-type]

    assert cursor.cached_rows == [[-7, "wide λ"]]


@pytest.mark.skipif(not _cstate._HAVE_C_EXT, reason="C extension is not built")
def test_dbos_batch_decodes_varying_offsets_and_leaves_partial_frame() -> None:
    desc = _descriptor()
    payloads = [
        _row_payload(1, "a"),
        _row_payload(2, "a much longer value"),
        _row_payload(3, None, null_text=True),
    ]
    complete = b"".join(_dbos_frame(payload) for payload in payloads)
    incomplete = _dbos_frame(_row_payload(4, "partial"))[:-2]
    # Keep the frame header intact while leaving part of its payload unread.
    view = memoryview(complete + incomplete)

    rows, consumed = _cstate._c_ext.process_dbos_batch(
        view,
        desc.field_type,
        desc.field_size,
        desc.field_trueSize,
        desc.field_offset,
        desc.field_fixedSize,
        desc.field_physField,
        desc.numFields,
        desc.nullsAllowed,
        desc.fixedFieldsSize,
        desc.numVaryingFields,
        "utf-8",
        "utf-8",
    )

    assert rows == [[1, "a"], [2, "a much longer value"], [3, None]]
    assert consumed == len(complete)


@pytest.mark.asyncio
async def test_protocol_passes_dbos_payload_view_directly_to_decoder(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = Connection()
    conn.log = logging.getLogger("test.dbos_view")
    payload = memoryview(_row_payload(81, "view"))
    inner_header = b"\x00" * 4 + struct.pack(">i", len(payload))

    class ViewStream:
        def read_view_sync(self, size: int) -> memoryview | None:
            if size == 8:
                return memoryview(inner_header)
            return None

        async def read_view(self, size: int) -> memoryview:
            assert size == len(payload)
            return payload

        def read_available_view(self) -> None:
            return None

        def advance_head(self, _size: int) -> None:
            raise AssertionError("no batched rows are buffered")

    conn._stream = ViewStream()  # type: ignore[assignment]
    conn.tupdesc = _descriptor()
    headers = deque([b"Y\x00\x00\x00\x00", b"Z\x00\x00\x00\x00"])

    async def read_header(size: int) -> bytes:
        assert size == 5
        return headers.popleft()

    conn._read = read_header  # type: ignore[method-assign]
    seen: list[type] = []

    def record_payload(cursor, _tupdesc, data) -> None:
        seen.append(type(data))
        cursor.cached_rows.append(["decoded"])

    monkeypatch.setattr(conn._dbos, "_process_dbos_payload", record_payload)
    cursor = conn.cursor()
    states = [state async for state in conn._protocol._connNextResultSetGenerator(cursor)]

    assert states == ["DATA_BATCH", "READY_FOR_QUERY"]
    assert seen == [memoryview]
    assert list(cursor.cached_rows) == [["decoded"]]
