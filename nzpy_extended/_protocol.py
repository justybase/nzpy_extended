"""Wire-protocol message handlers and the main result-set generator.

``ProtocolHandler`` owns:
  - All ``handle_*`` methods that process individual wire-protocol messages
  - The ``message_types`` dispatch dictionary
  - The long-running async generator ``_connNextResultSetGenerator``
  - ``_drain_socket``
  - ``handle_messages``, ``close_prepared_statement``, ``_send_message``
"""

from __future__ import annotations

import asyncio
import os
import stat
from typing import TYPE_CHECKING, Any, Type

from .exceptions import (
    ConnectionClosedError,
    DataError,
    Error,
    IntegrityError,
    InterfaceError,
    InternalError,
    OperationalError,
    ProgrammingError,
)
from .protocol import (
    BIND_COMPLETE,
    CLOSE,
    CLOSE_COMPLETE,
    COMMAND_COMPLETE,
    CONN_EXECUTING,
    COPY_DATA,
    COPY_DONE,
    COPY_DONE_MSG,
    COPY_IN_RESPONSE,
    COPY_OUT_RESPONSE,
    DATA_ROW,
    EMPTY_QUERY_RESPONSE,
    ERROR_RESPONSE,
    EXECUTE_MSG,
    FLUSH_MSG,
    IDLE,
    NOTICE_RESPONSE,
    NOTIFICATION_RESPONSE,
    NO_DATA,
    NULL_BYTE,
    PARAMETER_DESCRIPTION,
    PARAMETER_STATUS,
    PARSE_COMPLETE,
    PORTAL_SUSPENDED,
    READY_FOR_QUERY,
    RESPONSE_CODE,
    ROW_DESCRIPTION,
    STATEMENT,
    SYNC_MSG,
)
from .types import DbosTupleDesc, Interval
from .utils import (
    bh_unpack,
    c_unpack,
    ci_unpack,
    h_unpack,
    i_pack,
    i_unpack,
    ihic_unpack,
    pg_to_py_encodings,
)
from ._serializers import build_pg_types, build_py_types
from . import _cstate

if TYPE_CHECKING:
    from .core import Connection, Cursor


class ProtocolHandler:
    """Handles wire-protocol message parsing and response dispatch.

    Created by ``Connection`` and stored as ``self._protocol``.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self.message_types: dict[bytes, Any] = {
            PARAMETER_STATUS: self.handle_PARAMETER_STATUS,
            READY_FOR_QUERY: self.handle_READY_FOR_QUERY,
            ROW_DESCRIPTION: self.handle_ROW_DESCRIPTION,
            ERROR_RESPONSE: self.handle_ERROR_RESPONSE,
            EMPTY_QUERY_RESPONSE: self.handle_EMPTY_QUERY_RESPONSE,
            DATA_ROW: self.handle_DATA_ROW,
            COMMAND_COMPLETE: self.handle_COMMAND_COMPLETE,
            PARSE_COMPLETE: self.handle_PARSE_COMPLETE,
            BIND_COMPLETE: self.handle_BIND_COMPLETE,
            CLOSE_COMPLETE: self.handle_CLOSE_COMPLETE,
            PORTAL_SUSPENDED: self.handle_PORTAL_SUSPENDED,
            NO_DATA: self.handle_NO_DATA,
            PARAMETER_DESCRIPTION: self.handle_PARAMETER_DESCRIPTION,
            NOTIFICATION_RESPONSE: self.handle_NOTIFICATION_RESPONSE,
            COPY_DONE: self.handle_COPY_DONE,
            COPY_DATA: self.handle_COPY_DATA,
            COPY_IN_RESPONSE: self.handle_COPY_IN_RESPONSE,
            COPY_OUT_RESPONSE: self.handle_COPY_OUT_RESPONSE,
        }

    # ------------------------------------------------------------------
    # Individual message handlers
    # ------------------------------------------------------------------

    async def handle_ERROR_RESPONSE(self, data: bytes, ps: Any) -> None:
        conn = self._conn
        msg = dict(
            (
                s[:1].decode(conn._client_encoding),
                s[1:].decode(conn._client_encoding),
            )
            for s in data.split(NULL_BYTE)
            if s != b""
        )
        response_code = msg.get(RESPONSE_CODE, "")
        cls: Type[Error]
        if response_code == "28000":
            cls = InterfaceError
        elif response_code == "23505":
            cls = IntegrityError
        elif response_code.startswith("08"):
            cls = OperationalError
        elif response_code.startswith("22"):
            cls = DataError
        elif response_code.startswith("26"):
            cls = InternalError
        else:
            cls = ProgrammingError
        conn.error = cls(msg)

    async def handle_EMPTY_QUERY_RESPONSE(self, data: bytes, ps: Any) -> None:
        self._conn.error = ProgrammingError("query was empty")

    async def handle_CLOSE_COMPLETE(self, data: bytes, ps: Any) -> None:
        pass

    async def handle_PARSE_COMPLETE(self, data: bytes, ps: Any) -> None:
        pass

    async def handle_BIND_COMPLETE(self, data: bytes, ps: Any) -> None:
        pass

    async def handle_PORTAL_SUSPENDED(self, data: bytes, cursor: Any) -> None:
        pass

    async def handle_PARAMETER_DESCRIPTION(self, data: bytes, ps: Any) -> None:
        pass

    async def _deliver_notice(
        self,
        cursor: Cursor,
        notice: str,
        *,
        append_empty_row: bool = False,
    ) -> None:
        conn = self._conn
        cursor.notices.append(notice)
        handler = cursor.notice_handler
        if handler is not None and callable(handler):
            try:
                handler(notice)
            except Exception as e:
                conn.log.warning("Error in notice_handler: %s", e)
        conn.log.debug("Response received from backend:%s", notice)
        if append_empty_row:
            cursor.cached_rows.append([])

    async def handle_COPY_DONE(self, data: bytes, ps: Any) -> None:
        self._conn._copy_done = True

    async def handle_COPY_OUT_RESPONSE(self, data: bytes, ps: Any) -> None:
        _, _ = bh_unpack(data)
        if ps.stream is None:
            raise InterfaceError("An output stream is required for the COPY OUT response.")

    async def handle_COPY_DATA(self, data: bytes, ps: Any) -> None:
        await asyncio.to_thread(ps.stream.write, data)

    async def handle_COPY_IN_RESPONSE(self, data: bytes, ps: Any) -> None:
        conn = self._conn
        _, _ = bh_unpack(data)
        if ps.stream is None:
            raise InterfaceError("An input stream is required for the COPY IN response.")
        bffr = bytearray(conn._buffer_size)
        while True:
            bytes_read = await asyncio.to_thread(ps.stream.readinto, bffr)
            if bytes_read == 0:
                break
            await conn._write(COPY_DATA + i_pack(bytes_read + 4))
            await conn._write(bffr[:bytes_read])
            await conn._flush()
        await conn._write(COPY_DONE_MSG)
        await conn._write(SYNC_MSG)
        await conn._flush()

    async def handle_NOTIFICATION_RESPONSE(self, data: bytes, ps: Any) -> None:
        conn = self._conn
        backend_pid = i_unpack(data)[0]
        idx = 4
        null = data.find(NULL_BYTE, idx) - idx
        condition = data[idx: idx + null].decode("ascii")
        idx += null + 1
        conn.notifications.append((backend_pid, condition))

    async def handle_READY_FOR_QUERY(self, data: bytes, ps: Any) -> None:
        self._conn.in_transaction = data != IDLE

    async def handle_ROW_DESCRIPTION(self, data: bytes, cursor: Cursor) -> None:
        conn = self._conn
        count = h_unpack(data)[0]
        idx = 2
        for _ in range(count):
            name = data[idx: data.find(NULL_BYTE, idx)]
            idx += len(name) + 1
            field: dict[str, Any] = dict(
                zip(
                    ("type_oid", "type_size", "type_modifier", "format"),
                    ihic_unpack(data, idx),
                )
            )
            field["name"] = name
            idx += 11
            cursor.ps["row_desc"].append(field)  # type: ignore[index]
            field["nzpy_extended_fc"] = conn.pg_types[field["type_oid"]][0]
            field["func"] = conn.pg_types[field["type_oid"]][1]

    async def handle_COMMAND_COMPLETE(self, data: bytes, cursor: Cursor) -> None:
        conn = self._conn
        values = data[:-1].split(b" ")
        command = values[0]
        if command in conn._commands_with_count:
            row_count = int(values[-1])
            if cursor.row_count == -1:
                cursor.row_count = row_count
            else:
                cursor.row_count += row_count
        if command in (b"ALTER", b"CREATE"):
            for scache in conn._caches.values():
                for pcache in scache.values():
                    for ps in pcache["ps"].values():
                        await self.close_prepared_statement(ps["statement_name_bin"])
                    pcache["ps"].clear()

    async def handle_DATA_ROW(self, data: bytes, cursor: Cursor) -> None:
        from .types import decimalToBinary as _decimalToBinary
        from .utils import ihic_unpack as _ihic_unpack

        numberofcol = len(cursor.ps["row_desc"])  # type: ignore[index]
        bitmaplen = numberofcol // 8
        if numberofcol % 8 > 0:
            bitmaplen += 1

        hex_str = data[0:bitmaplen].hex()
        dec = int(hex_str, 16)
        bitmap = _decimalToBinary(dec, bitmaplen * 8)
        bitmap.reverse()

        data_idx = bitmaplen
        row: list[Any] = []
        row_desc = cursor.ps["row_desc"]  # type: ignore[index]
        for i, func in enumerate(cursor.ps["input_funcs"]):  # type: ignore[index]
            if bitmap[i] == 0:
                row.append(None)
            else:
                vlen = i_unpack(data, data_idx)[0]
                data_idx += 4
                if vlen < 4:
                    raise InterfaceError(
                        f"Invalid data row: vlen={vlen} for column {i} (min 4 expected)"
                    )
                val = func(data, data_idx, vlen - 4)
                data_idx += vlen - 4
                if row_desc[i]["type_oid"] == 1042:
                    mod = row_desc[i]["type_modifier"]
                    if mod > 0:
                        pad_to = (mod >> 16) & 0xFFFF
                        if pad_to > 0:
                            val = val.rstrip("\x00").ljust(pad_to)
                row.append(val)
        cursor.cached_rows.append(row)

    async def handle_PARAMETER_STATUS(self, data: bytes, ps: Any) -> None:
        from .types import (
            FC_BINARY,
            FC_TEXT,
            interval_recv_float,
            interval_recv_integer,
            interval_send_float,
            interval_send_integer,
            timestamp_in,
            timestamp_send_float,
            timestamp_send_integer,
            timestamptz_in,
            timestamptz_send_float,
            timestamptz_send_integer,
        )
        from datetime import timedelta as Timedelta
        from looseversion import LooseVersion

        conn = self._conn
        pos = data.find(NULL_BYTE)
        key, value = data[:pos], data[pos + 1:-1]
        conn.parameter_statuses.append((key, value))

        if key == b"client_encoding":
            encoding = value.decode("ascii").lower()
            conn._client_encoding = pg_to_py_encodings.get(encoding) or encoding
            conn._char_varchar_encoding = conn._client_encoding

        elif key == b"integer_datetimes":
            if value == b"on":
                conn.py_types[1114] = (1114, FC_BINARY, timestamp_send_integer)
                conn.pg_types[1114] = (FC_TEXT, timestamp_in)
                conn.py_types[1184] = (1184, FC_BINARY, timestamptz_send_integer)
                conn.pg_types[1184] = (FC_TEXT, timestamptz_in)
                conn.py_types[Interval] = (1186, FC_BINARY, interval_send_integer)
                conn.py_types[Timedelta] = (1186, FC_BINARY, interval_send_integer)
                conn.pg_types[1186] = (FC_TEXT, interval_recv_integer)
            else:
                conn.py_types[1114] = (1114, FC_BINARY, timestamp_send_float)
                conn.pg_types[1114] = (FC_TEXT, timestamp_in)
                conn.py_types[1184] = (1184, FC_BINARY, timestamptz_send_float)
                conn.pg_types[1184] = (FC_TEXT, timestamptz_in)
                conn.py_types[Interval] = (1186, FC_BINARY, interval_send_float)
                conn.py_types[Timedelta] = (1186, FC_BINARY, interval_send_float)
                conn.pg_types[1186] = (FC_TEXT, interval_recv_float)

        elif key == b"server_version":
            conn._server_version = LooseVersion(value.decode("ascii"))
            if conn._server_version < LooseVersion("8.2.0"):
                conn._commands_with_count = (
                    b"INSERT",
                    b"DELETE",
                    b"UPDATE",
                    b"MOVE",
                    b"FETCH",
                )
            elif conn._server_version < LooseVersion("9.0.0"):
                conn._commands_with_count = (
                    b"INSERT",
                    b"DELETE",
                    b"UPDATE",
                    b"MOVE",
                    b"FETCH",
                    b"COPY",
                )

    def handle_NO_DATA(self, msg: bytes, ps: Any) -> None:
        pass

    # ------------------------------------------------------------------
    # Dispatch loop
    # ------------------------------------------------------------------

    async def handle_messages(self, cursor: Cursor) -> None:
        conn = self._conn
        code: bytes | None = None
        conn.error = None

        while code != READY_FOR_QUERY:
            code, data_len = ci_unpack(await conn._read(5))
            if not isinstance(code, bytes):
                raise InterfaceError("protocol message code missing")
            await self.message_types[code](await conn._read(data_len - 4), cursor)

        if conn.error is not None:
            raise conn.error

    async def close_prepared_statement(self, statement_name_bin: bytes) -> None:
        conn = self._conn
        await self._send_message(CLOSE, STATEMENT + statement_name_bin)
        await conn._write(SYNC_MSG)
        await conn._flush()
        await self.handle_messages(conn._cursor)

    # ------------------------------------------------------------------
    # Protocol generators
    # ------------------------------------------------------------------

    async def _connNextResultSetGenerator(self, cursor: Cursor) -> Any:
        conn = self._conn
        stream = conn._stream
        assert stream is not None
        fname = None
        fh = None
        conn._cached_header = None

        while True:
            if conn._cached_header is not None:
                header = conn._cached_header
                conn._cached_header = None
            else:
                header = await conn._read(5)
            response = header[:1]

            if response == COMMAND_COMPLETE:
                length = i_unpack(await conn._read(4))[0]
                data = await conn._read(length)
                await self.handle_COMMAND_COMPLETE(data, cursor)
                conn.log.debug(
                    "Response received from backend: %s",
                    str(data, conn._client_encoding),
                )
                yield "COMMAND_COMPLETE"
                continue
            if response == READY_FOR_QUERY:
                conn._dirty_socket = False
                conn._active_generator = None
                conn._active_cursor = None
                yield "READY_FOR_QUERY"
                return
            if response == b"L":
                conn._dirty_socket = False
                conn._active_generator = None
                conn._active_cursor = None
                yield "READY_FOR_QUERY"
                return
            if response == b"0":
                length = i_unpack(await conn._read(4))[0]
                await conn._read(length)
                conn.log.debug("Unknown response code '0' received from backend")
                continue
            if response == b"A":
                length = i_unpack(await conn._read(4))[0]
                data = await conn._read(length)
                await self.handle_NOTIFICATION_RESPONSE(data, cursor)
                conn.log.debug("Notification received from backend")
                continue
            if response == b"P":
                length = i_unpack(await conn._read(4))[0]
                conn.log.debug(
                    "Response received from backend:%s",
                    str(await conn._read(length), conn._client_encoding),
                )
                continue
            if response == ERROR_RESPONSE:
                length = i_unpack(await conn._read(4))[0]
                data = await conn._read(length)
                await self.handle_ERROR_RESPONSE(data, cursor)
                conn.log.debug("Response received from backend:%s", conn.error)
                yield "ERROR"
                continue
            if response == ROW_DESCRIPTION:
                length = i_unpack(await conn._read(4))[0]
                prev_tupdesc = cursor.ps.get("tupdesc") if cursor.ps else None
                cursor.ps = {"row_desc": [], "tupdesc": prev_tupdesc}
                await self.handle_ROW_DESCRIPTION(await conn._read(length), cursor)
                cursor.ps["input_funcs"] = list(
                    f["func"] for f in cursor.ps["row_desc"]
                )
                yield "ROW_DESCRIPTION"
            if response == DATA_ROW:
                length = i_unpack(await conn._read(4))[0]
                await self.handle_DATA_ROW(await conn._read(length), cursor)
                cursor.has_rows = True
                yield "DATA_ROW"
            if response == b"X":
                length = i_unpack(await conn._read(4))[0]
                conn.tupdesc = DbosTupleDesc()
                conn._dbos.Res_get_dbos_column_descriptions(
                    await conn._read(length), conn.tupdesc
                )
                if cursor.ps is not None:
                    cursor.ps["tupdesc"] = conn.tupdesc
                    if len(cursor.ps.get("row_desc", [])) == 0:
                        for i in range(conn.tupdesc.numFields):
                            cursor.ps["row_desc"].append({
                                "name": b"",
                                "type_oid": conn.tupdesc.field_type[i],
                                "type_size": conn.tupdesc.field_size[i],
                                "type_modifier": -1,
                                "format": 0,
                            })
                yield "ROW_DESCRIPTION"
                continue
            if response == b"Y":
                inner_header = stream.read_view_sync(8)
                if inner_header is None:
                    inner_header = await conn._read(8)
                tup_len = i_unpack(inner_header, 4)[0]
                data = stream.read_view_sync(tup_len)
                if data is None:
                    data = await conn._read(tup_len)
                conn._dbos._process_dbos_payload(
                    cursor, conn.tupdesc, bytes(data)
                )

                if _cstate._HAVE_C_EXT:
                    assert _cstate._c_ext is not None
                    view = stream.read_available_view()
                    if view is not None and len(view) > 13:
                        rows, consumed = _cstate._c_ext.process_dbos_batch(
                            view,
                            conn.tupdesc.field_type,
                            conn.tupdesc.field_size,
                            conn.tupdesc.field_trueSize,
                            conn.tupdesc.field_offset,
                            conn.tupdesc.field_fixedSize,
                            conn.tupdesc.field_physField,
                            conn.tupdesc.numFields,
                            conn.tupdesc.nullsAllowed,
                            conn.tupdesc.fixedFieldsSize,
                            conn.tupdesc.numVaryingFields,
                            conn._char_varchar_encoding,
                            conn._client_encoding,
                        )
                        if rows:
                            cursor.cached_rows.extend(rows)
                            stream.advance_head(consumed)

                header = stream.read_view_sync(5)
                if header is None:
                    header = await conn._read(5)
                conn._cached_header = header
                if len(cursor.cached_rows) > 0:
                    cursor.has_rows = True
                yield "DATA_BATCH"
                continue
            if response == b"u":
                await conn._read(10)
                await conn._read(16)
                length = i_unpack(await conn._read(4))[0]
                fnameBuf = await conn._read(length)
                fname = str(fnameBuf, conn._client_encoding)
                try:
                    stat_result = (
                        await asyncio.to_thread(os.stat, fname)
                        if os.path.exists(fname)
                        else None
                    )
                    is_fifo = stat.S_ISFIFO(stat_result.st_mode) if stat_result else False
                    if is_fifo:
                        fh = await asyncio.to_thread(open, fname, "wb")
                    else:
                        fh = await asyncio.to_thread(open, fname, "wb+")  # type: ignore[arg-type]
                    conn.log.debug("Successfully opened file: %s", fname)
                    buf = bytearray(i_pack(0))
                    await conn._write(buf)
                    await conn._flush()
                except Exception:
                    conn.log.warning("Error while opening file")

            if response == b"U":
                if fh is not None:
                    await conn._extab.receiveAndWriteDatatoExternal(fname, fh)
                yield "EXTAB_DATA"

            if response == b"l":
                await conn._extab.xferTable()
                yield "EXTAB_IMPORT"

            if response == b"x":
                await conn._read(4)
                conn.log.warning("Error operation cancel")
                yield "EXTAB_CANCEL"

            if response == b"e":
                length = i_unpack(await conn._read(4))[0]
                logDir = str(await conn._read(length - 1), conn._client_encoding)
                await conn._read(1)
                char = c_unpack(await conn._read(1))[0]
                filenameBuf = bytearray(char)
                while True:
                    char = c_unpack(await conn._read(1))[0]
                    if char == b"\x00":
                        break
                    filenameBuf.extend(char)
                filename = str(filenameBuf, conn._client_encoding)
                logType = i_unpack(await conn._read(4))[0]
                if not await conn._extab.getFileFromBE(logDir, filename, logType):
                    conn.log.debug("Error in writing file received from BE")
                continue

            if response == NOTICE_RESPONSE:
                length = i_unpack(await conn._read(4))[0]
                notice = str(await conn._read(length), conn._client_encoding)
                if notice.startswith("NOTICE:"):
                    notice = notice[len("NOTICE:"):]
                notice = notice.strip().rstrip("\x00")
                await self._deliver_notice(cursor, notice)
                yield "NOTICE"

            if response == b"I":
                length = i_unpack(await conn._read(4))[0]
                notice = str(await conn._read(length), conn._client_encoding)
                if notice.startswith("NOTICE:"):
                    notice = notice[len("NOTICE:"):]
                notice = notice.strip().rstrip("\x00")
                await self._deliver_notice(cursor, notice, append_empty_row=True)
                yield "NOTICE"

            if response == b"s":
                length = i_unpack(await conn._read(4))[0]
                await conn._read(length)
                continue

    async def _drain_socket(self) -> None:
        """Consume orphaned backend messages up to ReadyForQuery.

        Raises InterfaceError if ReadyForQuery is not reached within ~2s.
        """
        conn = self._conn
        assert conn._stream is not None
        conn.log.debug("Draining dirty socket stream...")

        deadline = asyncio.get_event_loop().time() + 2.0

        async def _read_exact(n: int) -> bytes:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise InterfaceError(
                    "Connection protocol out of sync: orphaned response incomplete "
                    "(no ReadyForQuery). Reconnect required."
                )
            try:
                return await asyncio.wait_for(conn._read(n), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise InterfaceError(
                    "Connection protocol out of sync: orphaned response incomplete "
                    "(no ReadyForQuery). Reconnect required."
                ) from exc

        try:
            # Discard leading null padding already buffered (and any still on the socket
            # that arrives as part of the first reads below).
            conn._stream.discard_buffered_leading_nulls()

            cached_header = None
            while True:
                if asyncio.get_event_loop().time() > deadline:
                    raise InterfaceError(
                        "Connection protocol out of sync: orphaned response incomplete "
                        "(no ReadyForQuery). Reconnect required."
                    )

                if cached_header is not None:
                    header = cached_header
                    cached_header = None
                else:
                    # Skip null padding between messages / after previous RFQ.
                    while True:
                        b = await _read_exact(1)
                        if b != b"\x00":
                            break
                    header = b + await _read_exact(4)

                response = header[:1]

                conn.log.debug("Drain read msg code: %s", response)

                if response in (READY_FOR_QUERY, b"L"):
                    conn.status = CONN_EXECUTING
                    conn._dirty_socket = False
                    conn.log.debug("Socket successfully drained to READY_FOR_QUERY.")
                    return

                if response in (
                    COMMAND_COMPLETE,
                    b"P",
                    ERROR_RESPONSE,
                    ROW_DESCRIPTION,
                    DATA_ROW,
                    b"X",
                ):
                    length = i_unpack(await _read_exact(4))[0]
                    if length > 0:
                        await _read_exact(length)
                    continue

                if response == b"Y":
                    inner_header = conn._stream.read_view_sync(8)
                    if inner_header is None:
                        inner_header = await _read_exact(8)
                    tup_len = i_unpack(inner_header, 4)[0]
                    data = conn._stream.read_view_sync(tup_len)
                    if data is None:
                        await _read_exact(tup_len)
                    while True:
                        header = conn._stream.read_view_sync(5)
                        if header is None:
                            # Skip nulls then read type+4
                            while True:
                                b = await _read_exact(1)
                                if b != b"\x00":
                                    break
                            header = b + await _read_exact(4)
                        if header[:1] != b"Y":
                            cached_header = header
                            break
                        inner_header = conn._stream.read_view_sync(8)
                        if inner_header is None:
                            inner_header = await _read_exact(8)
                        tup_len = i_unpack(inner_header, 4)[0]
                        data = conn._stream.read_view_sync(tup_len)
                        if data is None:
                            await _read_exact(tup_len)
                    continue

                if response == b"u":
                    await _read_exact(10)
                    await _read_exact(16)
                    length = i_unpack(await _read_exact(4))[0]
                    if length > 0:
                        await _read_exact(length)
                    continue

                if response == b"U":
                    try:
                        await conn._extab.receiveAndWriteDatatoExternal(None, None)
                    except Exception:
                        pass
                    continue

                if response == b"l":
                    await conn._extab.xferTable()
                    continue

                if response == b"x":
                    await _read_exact(4)
                    continue

                if response == b"e":
                    length = i_unpack(await _read_exact(4))[0]
                    await _read_exact(length - 1)
                    await _read_exact(1)
                    while True:
                        char = await _read_exact(1)
                        if char == b"\x00":
                            break
                    await _read_exact(4)
                    while True:
                        numBytes = i_unpack(await _read_exact(4))[0]
                        if numBytes == 0:
                            break
                        await _read_exact(numBytes)
                    continue

                if response in (NOTICE_RESPONSE, b"I"):
                    length = i_unpack(await _read_exact(4))[0]
                    if length > 0:
                        await _read_exact(length)
                    continue

                if response in (b"0", b"A"):
                    # Same framing as _connNextResultSetGenerator: length + payload.
                    length = i_unpack(await _read_exact(4))[0]
                    if length > 0:
                        await _read_exact(length)
                    continue

                length = i_unpack(await _read_exact(4))[0]
                if length > 0:
                    await _read_exact(length)

        except InterfaceError:
            raise
        except Exception as e:
            raise InterfaceError(
                f"Connection protocol out of sync while draining orphaned response: {e}. "
                "Reconnect required."
            ) from e

    # ------------------------------------------------------------------
    # Outbound helpers
    # ------------------------------------------------------------------

    async def send_EXECUTE(self, cursor: Cursor) -> None:
        conn = self._conn
        await conn._write(EXECUTE_MSG)
        await conn._write(FLUSH_MSG)

    async def _send_message(self, code: bytes, data: bytes) -> None:
        conn = self._conn
        try:
            await conn._write(code)
            await conn._write(i_pack(len(data) + 4))
            await conn._write(data)
            await conn._write(FLUSH_MSG)
        except ValueError as e:
            if str(e) == "write to closed file":
                raise ConnectionClosedError()
            raise
        except AttributeError:
            raise ConnectionClosedError()


__all__ = [
    "ProtocolHandler",
]
