"""DBOS (Database Operating System) binary row parsing.

Two code paths are provided:
  1. **native extension** (fast) — delegates to ``rust_ext.process_dbos_row`` /
     ``rust_ext.process_dbos_batch``
  2. **Pure-Python fallback** — ``_build_dbos_row_python``, a line-by-line
     reimplementation that handles every NzType variant.

The ``DbosParser`` class owns the DBOS tuple descriptor and row-parsing
methods that previously lived on ``Connection``.
"""

from __future__ import annotations

import codecs
import functools
from datetime import date, datetime as Datetime, time
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from . import _native
from .exceptions import DataError, InterfaceError
from .protocol import (
    NzTypeBool,
    NzTypeChar,
    NzTypeDate,
    NzTypeDouble,
    NzTypeFloat,
    NzTypeGeometry,
    NzTypeInt,
    NzTypeInt1,
    NzTypeInt2,
    NzTypeInt8,
    NzTypeInterval,
    NzTypeJson,
    NzTypeJsonb,
    NzTypeJsonpath,
    NzTypeNChar,
    NzTypeNVarChar,
    NzTypeNumeric,
    NzTypeTime,
    NzTypeTimeTz,
    NzTypeTimestamp,
    NzTypeUnknown,
    NzTypeVarBinary,
    NzTypeVarChar,
    NzTypeVarFixedChar,
    NzTypeVector,
)
from .types import (
    DbosTupleDesc,
    Interval,
    J2000_OFFSET,
    j2date,
    time2struct,
    timestamp2struct,
    timetz_out_timetzadt,
)
from .utils import h_le_unpack, i_le_unpack, q_le_unpack

if TYPE_CHECKING:
    from .core import Connection, Cursor


# ---------------------------------------------------------------------------
# DbosParser
# ---------------------------------------------------------------------------

_VAR_LENGTH_TYPES = (
    NzTypeNChar,
    NzTypeNVarChar,
    NzTypeVarChar,
    NzTypeVarFixedChar,
    NzTypeGeometry,
    NzTypeVarBinary,
    NzTypeJson,
    NzTypeJsonb,
    NzTypeJsonpath,
    NzTypeVector,
)


def _layout_error(message: str) -> InterfaceError:
    """A malformed row layout is a framing fault: the session must be retired."""
    return InterfaceError(
        f"Invalid RowStandard payload: {message}; reconnect is required.")


class _ValueCache:
    """Bounded per-column cache for immutable decoded values.

    The key is the exact wire bytes. A hit returns the same Python object, so
    repeated strings, dates and Decimals do not allocate or parse again. The
    cache disables itself when the probe finds less than 2% repetition.
    """

    __slots__ = (
        "_entries",
        "_last_key",
        "_last_value",
        "_lookups",
        "_hits",
        "_enabled",
        "_max_entries",
        "_max_key",
        "_probe",
    )

    def __init__(self, max_entries: int = 4096, max_key: int = 64,
                 probe: int = 8192) -> None:
        self._entries: dict[bytes, Any] = {}
        self._last_key: bytes | None = None
        self._last_value: Any = None
        self._lookups = 0
        self._hits = 0
        self._enabled = True
        self._max_entries = max_entries
        self._max_key = max_key
        self._probe = probe

    def get_or_create(self, key: bytes, factory: Any) -> Any:
        if not self._enabled:
            return factory()
        if len(key) > self._max_key:
            self._record(False)
            return factory()
        if self._last_key == key:
            self._record(True)
            return self._last_value
        cached = self._entries.get(key)
        if cached is not None:
            self._last_key = key
            self._last_value = cached
            self._record(True)
            return cached
        value = factory()
        self._last_key = key
        self._last_value = value
        if len(self._entries) < self._max_entries:
            self._entries[key] = value
        self._record(False)
        return value

    def _record(self, hit: bool) -> None:
        self._lookups += 1
        if hit:
            self._hits += 1
        if self._lookups == self._probe and self._hits * 50 < self._lookups:
            self._enabled = False
            self._entries.clear()


def _column_value(tupdesc: Any, index: int, key: bytes, factory: Any) -> Any:
    caches = tupdesc._value_caches
    if index >= len(caches):
        caches.extend([None] * (index + 1 - len(caches)))
    cache = caches[index]
    if cache is None:
        cache = caches[index] = _ValueCache()
    return cache.get_or_create(key, factory)


@functools.lru_cache(maxsize=16)
def _text_codec(name: str) -> str:
    """Codec name for ``str(data, name)``.

    ``latin`` and ``latin-1`` are the same codec, but CPython decodes ``latin-1``
    on a fast path and ``latin`` through the general codec lookup (about 6x
    slower). Unknown names are returned unchanged so the caller raises as before.
    """
    try:
        canonical = codecs.lookup(name).name
    except LookupError:
        return name
    return "latin-1" if canonical == "iso8859-1" else name


class DbosParser:
    """Parses DBOS (Netezza binary row) payloads.

    Instances are created by ``Connection`` and kept as ``self._dbos``.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    # ----- Tuple-description parsing ----------------------------------------

    @staticmethod
    def Res_get_dbos_column_descriptions(data: bytes, tupdesc: DbosTupleDesc) -> None:
        """Parse the DBOS *X* message (column-description payload)."""
        from .utils import i_unpack

        tupdesc._native_decoder = None
        tupdesc._value_caches = []

        if len(data) < 36:
            raise InterfaceError(
                "Invalid DBOS column description: header is truncated")
        data_idx = 0
        tupdesc.version = i_unpack(data, data_idx)[0]
        tupdesc.nullsAllowed = i_unpack(data, data_idx + 4)[0]
        tupdesc.sizeWord = i_unpack(data, data_idx + 8)[0]
        tupdesc.sizeWordSize = i_unpack(data, data_idx + 12)[0]
        tupdesc.numFixedFields = i_unpack(data, data_idx + 16)[0]
        tupdesc.numVaryingFields = i_unpack(data, data_idx + 20)[0]
        tupdesc.fixedFieldsSize = i_unpack(data, data_idx + 24)[0]
        tupdesc.maxRecordSize = i_unpack(data, data_idx + 28)[0]
        tupdesc.numFields = i_unpack(data, data_idx + 32)[0]

        data_idx += 36
        if tupdesc.numFields is None:
            return
        if tupdesc.numFields < 0:
            raise InterfaceError(
                "Invalid DBOS column description: negative field count")
        required = 36 + tupdesc.numFields * 36 + 8
        if required > len(data):
            raise InterfaceError(
                "Invalid DBOS column description: field table is truncated")
        if tupdesc.numVaryingFields is None or tupdesc.numVaryingFields < 0:
            raise InterfaceError(
                "Invalid DBOS column description: negative varying field count")
        for _ in range(tupdesc.numFields):
            tupdesc.field_type.append(i_unpack(data, data_idx)[0])
            tupdesc.field_size.append(i_unpack(data, data_idx + 4)[0])
            tupdesc.field_trueSize.append(i_unpack(data, data_idx + 8)[0])
            tupdesc.field_offset.append(i_unpack(data, data_idx + 12)[0])
            tupdesc.field_physField.append(i_unpack(data, data_idx + 16)[0])
            tupdesc.field_logField.append(i_unpack(data, data_idx + 20)[0])
            tupdesc.field_nullAllowed.append(i_unpack(data, data_idx + 24)[0])
            tupdesc.field_fixedSize.append(i_unpack(data, data_idx + 28)[0])
            tupdesc.field_springField.append(i_unpack(data, data_idx + 32)[0])
            data_idx += 36

        tupdesc.DateStyle = i_unpack(data, data_idx)[0]
        tupdesc.EuroDates = i_unpack(data, data_idx + 4)[0]

    # ----- Single-row dispatch (native-ext / pure-Python) ------------------------

    def _decoder(self, tupdesc: DbosTupleDesc) -> Any:
        """Native decoder for this result set, built once per descriptor."""
        decoder = tupdesc._native_decoder
        if decoder is None:
            assert _native._native_ext is not None
            conn = self._conn
            decoder = _native._native_ext.DbosDecoder(
                tupdesc.field_type,
                tupdesc.field_size,
                tupdesc.field_trueSize,
                tupdesc.field_offset,
                tupdesc.field_fixedSize,
                tupdesc.field_physField,
                tupdesc.numFields,
                tupdesc.nullsAllowed,
                tupdesc.fixedFieldsSize,
                tupdesc.numVaryingFields,
                conn._char_varchar_encoding,
                conn._client_encoding,
            )
            tupdesc._native_decoder = decoder
        return decoder

    def _process_dbos_payload(
        self,
        cursor: Cursor,
        tupdesc: DbosTupleDesc,
        data: bytes | memoryview,
    ) -> None:
        """Parse a single DBOS *Y* payload and append the row to
        ``cursor.cached_rows``.
        """
        if _native._HAVE_NATIVE:
            assert _native._native_ext is not None
            try:
                row = self._decoder(tupdesc).row(data)
            except BufferError as exc:
                raise _layout_error(str(exc)) from exc
            except (UnicodeDecodeError, ValueError, OverflowError, InvalidOperation) as exc:
                # The native decoder records the column as a note: "column <index>".
                column = next((note for note in getattr(exc, "__notes__", ())
                               if note.startswith("column ")), None)
                where = f" ({column})" if column else ""
                raise DataError(
                    f"cannot decode RowStandard value{where}: {exc}") from exc
            cursor.cached_rows.append(row)
            return

        self._build_dbos_row_python(cursor, tupdesc, memoryview(data), data)

    # ----- Row layout validation --------------------------------------------

    @staticmethod
    def _is_null(tupdesc: DbosTupleDesc, data: bytes | memoryview,
                 index: int) -> bool:
        if tupdesc.nullsAllowed == 0:
            return False
        phys = tupdesc.field_physField[index]
        if phys < 0 or phys >= (tupdesc.numFields or 0):
            raise _layout_error(f"field {index} physical index is invalid")
        return ((data[2 + (phys >> 3)] >> (phys & 7)) & 1) == 1

    @staticmethod
    def _validate_field_span(
        tupdesc: DbosTupleDesc,
        index: int,
        start: int,
        data: bytes | memoryview,
    ) -> None:
        data_len = len(data)
        fldtype = tupdesc.field_type[index]
        if fldtype == NzTypeUnknown:
            fldtype = NzTypeVarChar
        fldsize = tupdesc.field_size[index]

        if fldtype == NzTypeChar:
            if fldsize < 0:
                raise _layout_error(f"field {index} size is invalid")
            if start + fldsize > data_len:
                raise _layout_error(f"field {index} extends beyond the row")
        elif fldtype in _VAR_LENGTH_TYPES:
            if start + 2 > data_len:
                raise _layout_error(f"field {index} length prefix is truncated")
            total = h_le_unpack(data, start)[0]
            if total < 2 or start + total > data_len:
                raise _layout_error(f"field {index} extends beyond the row")
        elif fldtype in (NzTypeInt8, NzTypeDouble):
            if start + 8 > data_len:
                raise _layout_error(f"field {index} extends beyond the row")
        elif fldtype == NzTypeInt:
            if start + 4 > data_len:
                raise _layout_error(f"field {index} extends beyond the row")
        elif fldtype == NzTypeInt2:
            if start + 2 > data_len:
                raise _layout_error(f"field {index} extends beyond the row")
        elif fldtype in (NzTypeInt1, NzTypeBool):
            if start + 1 > data_len:
                raise _layout_error(f"field {index} extends beyond the row")
        elif fldtype == NzTypeFloat:
            if start + 4 > data_len:
                raise _layout_error(f"field {index} extends beyond the row")
        elif fldtype in (NzTypeDate, NzTypeTime):
            if fldsize < 0:
                raise _layout_error(f"field {index} size is invalid")
            need = 8 if fldsize >= 8 else fldsize
            if start + need > data_len:
                raise _layout_error(f"field {index} extends beyond the row")
        elif fldtype == NzTypeTimestamp:
            if fldsize < 0:
                raise _layout_error(f"field {index} size is invalid")
            need = 8 if fldsize >= 8 else fldsize
            if start + need > data_len:
                raise _layout_error(f"field {index} extends beyond the row")
        elif fldtype in (NzTypeInterval, NzTypeTimeTz):
            if fldsize < 4:
                raise _layout_error(f"field {index} size is invalid")
            if start + fldsize > data_len:
                raise _layout_error(f"field {index} extends beyond the row")
        elif fldtype == NzTypeNumeric:
            chunk_len = tupdesc.field_trueSize[index]
            if chunk_len < 4 or chunk_len > 16 or chunk_len % 4 != 0:
                raise _layout_error(f"field {index} numeric width is invalid")
            if start + chunk_len > data_len:
                raise _layout_error(f"field {index} extends beyond the row")

    @classmethod
    def _validate_row_layout(
        cls,
        tupdesc: DbosTupleDesc,
        data: bytes | memoryview,
    ) -> list[int | None]:
        """Validate every field's storage and return each field's start.

        ``None`` marks a NULL field. Validation runs before any value is
        decoded so an invalid value in an early column cannot hide a malformed
        length or offset in a later one.
        """
        nfields = tupdesc.numFields
        if nfields is None or nfields < 0:
            raise _layout_error("field count is invalid")
        metadata = (
            tupdesc.field_type,
            tupdesc.field_size,
            tupdesc.field_trueSize,
            tupdesc.field_offset,
            tupdesc.field_fixedSize,
            tupdesc.field_physField,
        )
        if any(len(values) < nfields for values in metadata):
            raise _layout_error("field metadata is truncated")

        bitmaplen = (nfields + 7) // 8
        if 2 + bitmaplen > len(data):
            raise _layout_error("row is too short for the NULL bitmap")

        fixed_size = tupdesc.fixedFieldsSize
        if fixed_size is None or fixed_size < 0:
            raise _layout_error("fixed-field area offset is invalid")

        var_offsets: list[int] = []
        varying = tupdesc.numVaryingFields or 0
        if varying < 0:
            raise _layout_error("varying field count is invalid")
        current_voff = fixed_size
        for _ in range(varying):
            var_offsets.append(current_voff)
            if current_voff + 2 > len(data):
                raise _layout_error("varying field length is truncated")
            vlen = h_le_unpack(data, current_voff)[0]
            if vlen < 2:
                raise _layout_error("varying field length is invalid")
            next_voff = current_voff + vlen + (vlen & 1)
            if next_voff > len(data):
                raise _layout_error("varying field extends beyond the row")
            current_voff = next_voff

        starts: list[int | None] = []
        for index in range(nfields):
            if cls._is_null(tupdesc, data, index):
                starts.append(None)
                continue
            if tupdesc.field_fixedSize[index] != 0:
                if tupdesc.field_fixedSize[index] < 0:
                    raise _layout_error(f"field {index} fixed size is invalid")
                start = tupdesc.field_offset[index]
            elif var_offsets:
                var_index = tupdesc.field_offset[index]
                if var_index < 0 or var_index >= len(var_offsets):
                    raise _layout_error(f"field {index} varying index is invalid")
                start = var_offsets[var_index]
            else:
                start = fixed_size
            if start < 0 or start > len(data):
                raise _layout_error(f"field {index} starts outside the row")
            cls._validate_field_span(tupdesc, index, start, data)
            starts.append(start)
        return starts

    # ----- Pure-Python row parser -------------------------------------------

    def _build_dbos_row_python(
        self,
        cursor: Cursor,
        tupdesc: DbosTupleDesc,
        mv: memoryview,
        data: bytes | memoryview,
    ) -> None:
        """Pure-Python DBOS row decoder."""
        import struct

        conn = self._conn
        starts = self._validate_row_layout(tupdesc, data)
        row: list[Any] = []

        try:
            for cur_field, offset in enumerate(starts):
                if offset is None:
                    row.append(None)
                    continue

                fldtype = tupdesc.field_type[cur_field]
                if fldtype == NzTypeUnknown:
                    fldtype = NzTypeVarChar

                if fldtype == NzTypeChar:
                    fldlen = tupdesc.field_size[cur_field]
                    key = bytes(mv[offset: offset + fldlen])
                    def char_value() -> str:
                        value = str(key, _text_codec(conn._char_varchar_encoding))
                        return value.rstrip("\x00").ljust(fldlen)
                    row.append(_column_value(tupdesc, cur_field, key, char_value))
                elif fldtype in (NzTypeNChar, NzTypeNVarChar):
                    cursize = h_le_unpack(data, offset)[0] - 2
                    key = bytes(mv[offset + 2: offset + cursize + 2])
                    def nchar_value() -> str:
                        value = str(key, _text_codec(conn._client_encoding))
                        if fldtype == NzTypeNChar:
                            value = value.rstrip("\x00").ljust(tupdesc.field_size[cur_field])
                        return value
                    row.append(_column_value(tupdesc, cur_field, key, nchar_value))
                elif fldtype in (NzTypeVarBinary, NzTypeGeometry):
                    # Binary storage: the bytes as stored, no text decoding.
                    cursize = h_le_unpack(data, offset)[0] - 2
                    row.append(bytes(mv[offset + 2: offset + cursize + 2]))
                elif fldtype in (
                    NzTypeVarChar,
                    NzTypeVarFixedChar,
                    NzTypeJson,
                    NzTypeJsonb,
                    NzTypeJsonpath,
                    NzTypeVector,
                ):
                    cursize = h_le_unpack(data, offset)[0] - 2
                    key = bytes(mv[offset + 2: offset + cursize + 2])
                    row.append(_column_value(
                        tupdesc,
                        cur_field,
                        key,
                        lambda key=key: str(key, _text_codec(conn._char_varchar_encoding)),
                    ))
                elif fldtype == NzTypeInt8:
                    row.append(q_le_unpack(data, offset)[0])
                elif fldtype == NzTypeInt:
                    row.append(i_le_unpack(data, offset)[0])
                elif fldtype == NzTypeInt2:
                    # Signed: h_le_unpack is unsigned and would return 32768 for -32768.
                    row.append(struct.unpack_from("<h", data, offset)[0])
                elif fldtype == NzTypeInt1:
                    val = data[offset]
                    row.append(val - 256 if val > 127 else val)
                elif fldtype == NzTypeDouble:
                    row.append(struct.unpack_from("<d", data, offset)[0])
                elif fldtype == NzTypeFloat:
                    row.append(struct.unpack_from("<f", data, offset)[0])
                elif fldtype == NzTypeDate:
                    fldlen = tupdesc.field_size[cur_field]
                    key = bytes(mv[offset: offset + fldlen])
                    workspace = (
                        q_le_unpack(data, offset)[0]
                        if fldlen >= 8
                        else int.from_bytes(key, "little", signed=True)
                    )
                    year, month, day = j2date(workspace + J2000_OFFSET)

                    def date_object(
                        y: int = year, m: int = month, d: int = day
                    ) -> date:
                        return date(y, m, d)

                    row.append(_column_value(tupdesc, cur_field, key, date_object))
                elif fldtype == NzTypeTime:
                    fldlen = tupdesc.field_size[cur_field]
                    key = bytes(mv[offset: offset + fldlen])
                    workspace = (
                        q_le_unpack(data, offset)[0]
                        if fldlen >= 8
                        else int.from_bytes(key, "little", signed=True)
                    )
                    time_value = time2struct(workspace)
                    def time_value_object() -> Any:
                        if time_value[3]:
                            return time(
                                time_value[0], time_value[1], time_value[2], time_value[3]
                            )
                        return time(time_value[0], time_value[1], time_value[2])
                    row.append(_column_value(
                        tupdesc, cur_field, key, time_value_object
                    ))
                elif fldtype == NzTypeInterval:
                    fldlen = tupdesc.field_size[cur_field]
                    interval_time = (
                        q_le_unpack(data, offset)[0]
                        if fldlen >= 12
                        else int.from_bytes(mv[offset: offset + fldlen - 4], "little", signed=True)
                    )
                    interval_month = i_le_unpack(data, offset + fldlen - 4)[0]
                    row.append(
                        Interval(microseconds=interval_time, days=0, months=interval_month)
                    )
                elif fldtype == NzTypeTimeTz:
                    fldlen = tupdesc.field_size[cur_field]
                    key = bytes(mv[offset: offset + fldlen])
                    timetz_time = (
                        q_le_unpack(data, offset)[0]
                        if fldlen >= 12
                        else int.from_bytes(mv[offset: offset + fldlen - 4], "little", signed=True)
                    )
                    timetz_zone = i_le_unpack(data, offset + fldlen - 4)[0]
                    row.append(_column_value(
                        tupdesc,
                        cur_field,
                        key,
                        lambda t=timetz_time, z=timetz_zone: timetz_out_timetzadt(t, z),
                    ))
                elif fldtype == NzTypeTimestamp:
                    fldlen = tupdesc.field_size[cur_field]
                    key = bytes(mv[offset: offset + fldlen])
                    workspace = (
                        q_le_unpack(data, offset)[0]
                        if fldlen >= 8
                        else int.from_bytes(key, "little", signed=True)
                    )
                    if fldlen != 8:
                        raise ValueError(
                            f"TIMESTAMP storage is {fldlen} bytes, expected 8")
                    result = timestamp2struct(workspace)
                    def timestamp_value() -> Any:
                        if result is False:
                            return None
                        ts: tuple[int, int, int, int, int, int, int] = result  # type: ignore[assignment]
                        return Datetime(ts[0], ts[1], ts[2], ts[3], ts[4], ts[5], ts[6])
                    row.append(_column_value(
                        tupdesc, cur_field, key, timestamp_value
                    ))
                elif fldtype == NzTypeNumeric:
                    fsize = tupdesc.field_size[cur_field]
                    scale = fsize & 0x00FF
                    chunk_len = tupdesc.field_trueSize[cur_field]
                    count = chunk_len // 4
                    key = bytes(mv[offset: offset + count * 4])
                    words = [
                        int.from_bytes(
                            data[offset + i * 4: offset + (i + 1) * 4],
                            "little",
                            signed=False,
                        )
                        for i in range(count)
                    ]
                    val = 0
                    for w in words:
                        val = (val << 32) | w
                    total_bits = count * 32
                    if words[0] >> 31:
                        val -= 1 << total_bits
                    # Built from text: Decimal arithmetic would round to 28 digits.
                    row.append(_column_value(
                        tupdesc,
                        cur_field,
                        key,
                        lambda v=val, s=scale: Decimal(f"{v}E-{s}"),
                    ))
                elif fldtype == NzTypeBool:
                    row.append(data[offset] == 1)
                else:
                    row.append(None)

        except (UnicodeDecodeError, ValueError, OverflowError, struct.error,
                InvalidOperation) as exc:
            raise DataError(
                f"cannot decode RowStandard value (column {cur_field}): "
                f"{exc}") from exc

        cursor.cached_rows.append(row)

    # ----- Convenience ------------------------------------------------------

    async def Res_read_dbos_tuple(
        self,
        cursor: Cursor,
        tupdesc: DbosTupleDesc,
    ) -> None:
        """Read a single DBOS tuple from the wire and parse it."""
        conn = self._conn
        header = await conn._read(8)
        length = i_le_unpack(header, 4)[0]  # actually big-endian in protocol
        from .utils import i_unpack as _i_unpack

        length = _i_unpack(header, 4)[0]
        data = await conn._read(length)
        self._process_dbos_payload(cursor, tupdesc, data)

    # ----- CTable helpers ---------------------------------------------------

    @staticmethod
    def CTable_FieldAt(tupdesc: DbosTupleDesc, data: bytes, cur_field: int) -> bytes:
        if tupdesc.field_fixedSize[cur_field] != 0:
            return data[tupdesc.field_offset[cur_field]:]
        return DbosParser.CTable_i_varFieldPtr(
            data, tupdesc.fixedFieldsSize or 0, tupdesc.field_offset[cur_field]
        )

    @staticmethod
    def CTable_i_varFieldPtr(
        data: bytes, fixedOffset: int, varDex: int
    ) -> bytes:
        lenP = data[fixedOffset:]
        for _ in range(varDex):
            length = int.from_bytes(lenP[0:2], "little")
            if length % 2 == 0:
                lenP = lenP[length:]
            else:
                lenP = lenP[length + 1:]
        return lenP


__all__ = [
    "DbosParser",
]
