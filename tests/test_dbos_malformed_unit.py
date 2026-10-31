"""Malformed DBOS payloads: layout faults close, value faults defer.

The pure-Python decoder and the native extension must agree on every payload here.
Layout faults surface as InterfaceError (BufferError at the native boundary); value
faults surface as DataError.
"""

from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from nzpy_extended import _native
from nzpy_extended._dbos import DbosParser
from nzpy_extended.exceptions import DataError, InterfaceError
from nzpy_extended.protocol import NzTypeInt, NzTypeVarChar
from nzpy_extended.types import DbosTupleDesc

pytestmark = pytest.mark.unit

needs_native = pytest.mark.skipif(not _native._HAVE_NATIVE, reason="native extension is not built")


def _descriptor(phys: list[int] | None = None) -> DbosTupleDesc:
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
    desc.field_physField = phys if phys is not None else [0, 1]
    return desc


def _row(number: int, raw_varying: bytes, bitmap: int = 0) -> bytes:
    """Fixed header, bitmap, int at offset 3, then the varying area as given."""
    return b"\x00\x00" + bytes([bitmap]) + struct.pack("<i", number) + raw_varying


def _varying(text: bytes) -> bytes:
    length = len(text) + 2
    out = struct.pack("<H", length) + text
    return out + b"\x00" if length % 2 else out


def _parser() -> DbosParser:
    conn = SimpleNamespace(_char_varchar_encoding="utf-8", _client_encoding="utf-8")
    return DbosParser(conn)  # type: ignore[arg-type]


def _decode_python(payload: bytes, desc: DbosTupleDesc | None = None) -> list[object]:
    cursor = SimpleNamespace(cached_rows=[])
    _parser()._build_dbos_row_python(  # type: ignore[arg-type]
        cursor, desc or _descriptor(), memoryview(payload), payload
    )
    return cursor.cached_rows


def _decode_c(payload: bytes, desc: DbosTupleDesc | None = None) -> list[object]:
    desc = desc or _descriptor()
    return _native._native_ext.process_dbos_row(  # type: ignore[union-attr]
        payload,
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


VALID_PAYLOADS = [
    _row(5, _varying(b"x")),
    _row(-1, _varying(b"unicode \xc3\x9f"), bitmap=0),
    _row(9, _varying(b""), bitmap=0x02),  # NULL varying field keeps its prefix
]


@pytest.mark.parametrize("payload", VALID_PAYLOADS)
@needs_native
def test_valid_rows_decode_identically_in_c_and_python(payload: bytes) -> None:
    assert _decode_c(payload) == _decode_python(payload)[0]


MALFORMED_LAYOUTS = {
    "bitmap truncated": b"\x00",
    "varying prefix past end": b"\x00\x00\x00" + struct.pack("<i", 1),
    "varying length below minimum": _row(1, struct.pack("<H", 1)),
    "varying length past end": _row(1, struct.pack("<H", 200) + b"ab"),
}


@pytest.mark.parametrize("name", sorted(MALFORMED_LAYOUTS))
def test_malformed_layout_is_framing_error_in_python(name: str) -> None:
    payload = MALFORMED_LAYOUTS[name]
    with pytest.raises(InterfaceError, match="reconnect is required"):
        _decode_python(payload)


@pytest.mark.parametrize("name", sorted(MALFORMED_LAYOUTS))
@needs_native
def test_malformed_layout_is_buffer_error_in_c(name: str) -> None:
    payload = MALFORMED_LAYOUTS[name]
    with pytest.raises(BufferError, match="reconnect is required"):
        _decode_c(payload)


@pytest.mark.parametrize("name", sorted(MALFORMED_LAYOUTS))
def test_malformed_layout_maps_to_interface_error_through_parser(name: str) -> None:
    payload = MALFORMED_LAYOUTS[name]
    cursor = SimpleNamespace(cached_rows=[])
    with pytest.raises(InterfaceError):
        _parser()._process_dbos_payload(cursor, _descriptor(), payload)  # type: ignore[arg-type]
    assert cursor.cached_rows == []


def test_null_index_outside_field_table_is_framing_error() -> None:
    payload = _row(1, _varying(b"a"), bitmap=0)
    bad = _descriptor(phys=[0, 9])
    with pytest.raises(InterfaceError):
        _decode_python(payload, bad)


@needs_native
def test_null_index_outside_field_table_is_buffer_error_in_c() -> None:
    payload = _row(1, _varying(b"a"), bitmap=0)
    bad = _descriptor(phys=[0, 9])
    with pytest.raises(BufferError):
        _decode_c(payload, bad)


INVALID_UTF8_PAYLOAD = _row(7, struct.pack("<H", 4) + b"\xff\xfe")


def test_invalid_text_is_data_error_through_parser() -> None:
    cursor = SimpleNamespace(cached_rows=[])
    with pytest.raises(DataError):
        _parser()._process_dbos_payload(cursor, _descriptor(), INVALID_UTF8_PAYLOAD)  # type: ignore[arg-type]
    assert cursor.cached_rows == []


@needs_native
def test_invalid_text_is_data_error_in_c_parser() -> None:
    cursor = SimpleNamespace(cached_rows=[])
    with pytest.raises(DataError):
        _parser()._process_dbos_payload(cursor, _descriptor(), INVALID_UTF8_PAYLOAD)  # type: ignore[arg-type]
    assert cursor.cached_rows == []


@needs_native
def test_invalid_text_raises_unicode_error_from_c_codec() -> None:
    with pytest.raises(UnicodeDecodeError):
        _decode_c(INVALID_UTF8_PAYLOAD)


def _frame(payload: bytes) -> bytes:
    # Y, four reserved bytes, a second reserved word, then the big-endian length.
    return b"Y" + b"\x00" * 4 + b"\x00" * 4 + struct.pack(">i", len(payload)) + payload


@needs_native
def test_batch_stops_in_front_of_value_fault_so_caller_falls_back_to_row_path() -> None:
    desc = _descriptor()
    good = _frame(_row(1, _varying(b"a")))
    bad = _frame(INVALID_UTF8_PAYLOAD)
    args = (
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
    # The batch keeps the rows before the fault and leaves the faulty frame unconsumed.
    rows, consumed = _native._native_ext.process_dbos_batch(  # type: ignore[union-attr]
        memoryview(good + bad), *args
    )
    assert len(rows) == 1
    assert consumed == len(good)
    # Alone, the faulty row raises; the reader defers that error.
    with pytest.raises(UnicodeDecodeError):
        _native._native_ext.process_dbos_row(  # type: ignore[union-attr]
            memoryview(INVALID_UTF8_PAYLOAD), *args
        )


@needs_native
def test_batch_layout_fault_raises_buffer_error() -> None:
    desc = _descriptor()
    frame = _frame(MALFORMED_LAYOUTS["varying length past end"])
    with pytest.raises(BufferError):
        _native._native_ext.process_dbos_batch(  # type: ignore[union-attr]
            memoryview(frame),
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
