"""DBOS (Database Operating System) binary row parsing.

Two code paths are provided:
  1. **C extension** (fast) — delegates to ``c_ext.process_dbos_row`` /
     ``c_ext.process_dbos_batch``
  2. **Pure-Python fallback** — ``_build_dbos_row_python``, a line-by-line
     reimplementation that handles every NzType variant.

The ``DbosParser`` class owns the DBOS tuple descriptor and row-parsing
methods that previously lived on ``Connection``.
"""

from __future__ import annotations

from datetime import date, datetime as Datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from . import _cstate
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

    # ----- Single-row dispatch (C-ext / pure-Python) ------------------------

    def _process_dbos_payload(
        self,
        cursor: Cursor,
        tupdesc: DbosTupleDesc,
        data: bytes | memoryview,
    ) -> None:
        """Parse a single DBOS *Y* payload and append the row to
        ``cursor.cached_rows``.
        """
        conn = self._conn

        if _cstate._HAVE_C_EXT:
            assert _cstate._c_ext is not None
            row = _cstate._c_ext.process_dbos_row(  # pyright: ignore[reportUnknownMemberType]
                data,
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
            cursor.cached_rows.append(row)
            return

        self._build_dbos_row_python(cursor, tupdesc, memoryview(data), data)

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
        numFields = tupdesc.numFields
        if numFields is None:
            return
        nfields: int = numFields

        bitmaplen = nfields // 8
        if nfields % 8 > 0:
            bitmaplen += 1

        b_data = mv[2: 2 + bitmaplen]
        bitmap = [(b >> j) & 1 for b in b_data for j in range(8)]

        var_offsets: list[int] = []
        current_voff = tupdesc.fixedFieldsSize
        if current_voff is None:
            return
        for _ in range(
            tupdesc.numVaryingFields if tupdesc.numVaryingFields is not None else 0
        ):
            var_offsets.append(current_voff)
            if current_voff + 2 <= len(data):
                vlen = h_le_unpack(data, current_voff)[0]
                if vlen % 2 == 0:
                    current_voff += vlen
                else:
                    current_voff += vlen + 1

        field_lf = 0
        cur_field = 0
        row: list[Any] = []

        while field_lf < nfields and cur_field < nfields:
            if (
                bitmap[tupdesc.field_physField[field_lf]] == 1
                and tupdesc.nullsAllowed != 0
            ):
                row.append(None)
                cur_field += 1
                field_lf += 1
                continue

            if tupdesc.field_fixedSize[cur_field] != 0:
                offset = tupdesc.field_offset[cur_field]
            else:
                offset = var_offsets[tupdesc.field_offset[cur_field]]

            fldtype = tupdesc.field_type[cur_field]
            if fldtype == NzTypeUnknown:
                fldtype = NzTypeVarChar

            if fldtype == NzTypeChar:
                fldlen = tupdesc.field_size[cur_field]
                value = str(mv[offset: offset + fldlen], conn._char_varchar_encoding)
                value = value.rstrip("\x00").ljust(fldlen)
                row.append(value)
            elif fldtype in (NzTypeNChar, NzTypeNVarChar):
                cursize = h_le_unpack(data, offset)[0] - 2
                value = str(
                    mv[offset + 2: offset + cursize + 2], conn._client_encoding
                )
                if fldtype == NzTypeNChar:
                    value = value.rstrip("\x00").ljust(tupdesc.field_size[cur_field])
                row.append(value)
            elif fldtype in (
                NzTypeVarChar,
                NzTypeVarFixedChar,
                NzTypeGeometry,
                NzTypeVarBinary,
                NzTypeJson,
                NzTypeJsonb,
                NzTypeJsonpath,
                NzTypeVector,
            ):
                cursize = h_le_unpack(data, offset)[0] - 2
                value = str(
                    mv[offset + 2: offset + cursize + 2], conn._char_varchar_encoding
                )
                row.append(value)
            elif fldtype == NzTypeInt8:
                row.append(q_le_unpack(data, offset)[0])
            elif fldtype == NzTypeInt:
                row.append(i_le_unpack(data, offset)[0])
            elif fldtype == NzTypeInt2:
                row.append(h_le_unpack(data, offset)[0])
            elif fldtype == NzTypeInt1:
                val = data[offset]
                row.append(val - 256 if val > 127 else val)
            elif fldtype == NzTypeDouble:
                row.append(struct.unpack_from("<d", data, offset)[0])
            elif fldtype == NzTypeFloat:
                row.append(struct.unpack_from("<f", data, offset)[0])
            elif fldtype == NzTypeDate:
                fldlen = tupdesc.field_size[cur_field]
                workspace = (
                    q_le_unpack(data, offset)[0]
                    if fldlen >= 8
                    else int.from_bytes(mv[offset: offset + fldlen], "little", signed=True)
                )
                date_value = j2date(workspace + J2000_OFFSET)
                row.append(date(date_value[0], date_value[1], date_value[2]))
            elif fldtype == NzTypeTime:
                fldlen = tupdesc.field_size[cur_field]
                workspace = (
                    q_le_unpack(data, offset)[0]
                    if fldlen >= 8
                    else int.from_bytes(mv[offset: offset + fldlen], "little", signed=True)
                )
                time_value = time2struct(workspace)
                if time_value[3]:
                    row.append(
                        time(time_value[0], time_value[1], time_value[2], time_value[3])
                    )
                else:
                    row.append(time(time_value[0], time_value[1], time_value[2]))
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
                timetz_time = (
                    q_le_unpack(data, offset)[0]
                    if fldlen >= 12
                    else int.from_bytes(mv[offset: offset + fldlen - 4], "little", signed=True)
                )
                timetz_zone = i_le_unpack(data, offset + fldlen - 4)[0]
                row.append(timetz_out_timetzadt(timetz_time, timetz_zone))
            elif fldtype == NzTypeTimestamp:
                fldlen = tupdesc.field_size[cur_field]
                workspace = (
                    q_le_unpack(data, offset)[0]
                    if fldlen >= 8
                    else int.from_bytes(mv[offset: offset + fldlen], "little", signed=True)
                )
                if fldlen == 8:
                    result = timestamp2struct(workspace)
                    if result is not False:
                        ts: tuple[int, int, int, int, int, int, int] = result  # type: ignore[assignment]
                        row.append(
                            Datetime(ts[0], ts[1], ts[2], ts[3], ts[4], ts[5], ts[6])
                        )
                    else:
                        row.append(None)
                else:
                    row.append(None)
            elif fldtype == NzTypeNumeric:
                fsize = tupdesc.field_size[cur_field]
                scale = fsize & 0x00FF
                chunk_len = tupdesc.field_trueSize[cur_field]
                count = chunk_len // 4
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
                row.append(Decimal(val) * (Decimal(10) ** -scale))
            elif fldtype == NzTypeBool:
                row.append(data[offset] == 1)
            else:
                row.append(None)

            cur_field += 1
            field_lf += 1

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
