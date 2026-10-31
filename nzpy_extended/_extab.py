"""External table operations for Netezza COPY / UNLOAD.

``ExternalTableManager`` handles data streaming between the Python client
and the Netezza server for external-table import (``INSERT INTO ... FROM
EXTERNAL``) and export (``CREATE EXTERNAL TABLE ... AS SELECT``).

Previously these methods lived on ``Connection``.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from .protocol import (
    EXTERNAL_TABLE_STREAM_MARKER,
    EXTAB_SOCK_DATA,
    EXTAB_SOCK_DONE,
    EXTAB_SOCK_ERROR,
)
from .utils import c_unpack, h_pack, h_unpack, i_pack, i_unpack

if TYPE_CHECKING:
    from .core import Connection

# In-memory sources are split into pieces of this size before framing.
_SOURCE_PIECE_BYTES = 65536
_ERROR_FIELD_MAX = 1000


class ExternalTableManager:
    """Owns the ``xferTable``, ``receiveAndWriteDatatoExternal``, and
    ``getFileFromBE`` methods.

    An instance is created by ``Connection`` and kept as ``self._extab``.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    # ----- Inbound (server to client) — data reception ---------------------

    async def receiveAndWriteDatatoExternal(
        self,
        fname: str | None,
        fh: Any,
    ) -> None:
        """Receive data blocks from the backend and write them to *fh*."""
        conn = self._conn
        await conn._read(4)

        try:
            while True:
                try:
                    status = i_unpack(await conn._read(4))[0]
                except Exception as e:
                    conn.log.warning("Error while retrieving status: %s", str(e))
                    break

                if status == EXTAB_SOCK_DATA:
                    numBytes = i_unpack(await conn._read(4))[0]
                    try:
                        blockBuffer = await conn._read(numBytes)
                        if fh is not None:
                            await asyncio.to_thread(fh.write, blockBuffer)
                            await asyncio.to_thread(fh.flush)
                        conn.log.info("Successfully written %d bytes to file", numBytes)
                    except Exception as e:
                        conn.log.error(
                            "Error writing data to file '%s': %s", fname, str(e)
                        )
                        raise
                    continue

                elif status == EXTAB_SOCK_DONE:
                    conn.log.info("unload - done receiving data")
                    break

                elif status == EXTAB_SOCK_ERROR:
                    len_msg = h_unpack(await conn._read(2))[0]
                    errorMsg = str(await conn._read(len_msg), conn._client_encoding)

                    len_obj = h_unpack(await conn._read(2))[0]
                    errorObject = str(await conn._read(len_obj), conn._client_encoding)

                    conn.log.warning("unload - ErrorMsg: %s", errorMsg)
                    conn.log.warning("unload - ErrorObj: %s", errorObject)
                    break

                else:
                    conn.log.warning("unload - unexpected status: %d", status)
                    break

        finally:
            if fh is not None:
                try:
                    await asyncio.to_thread(fh.close)
                    conn.log.debug("Closed export file: %s", fname)
                except Exception as exc:
                    conn.log.debug(
                        "Error closing external table export file: %s",
                        exc,
                        exc_info=True,
                    )

    # ----- Outbound (client to server) — data transmission -----------------

    async def xferTable(self) -> None:
        """Handle the External Table transfer protocol (``l`` response).

        Supports both in-memory data sources
        (``self._conn._ext_table_source``) and on-disk file paths.
        """
        conn = self._conn
        await conn._read(4)
        clientversion = 1

        char = c_unpack(await conn._read(1))[0]
        filenameBuf = bytearray(char)
        while True:
            char = c_unpack(await conn._read(1))[0]
            if char == b"\x00":
                break
            filenameBuf.extend(char)

        filename = str(filenameBuf, conn._client_encoding)

        hostversion = i_unpack(await conn._read(4))[0]

        val = bytearray(i_pack(clientversion))
        await conn._write(val)
        await conn._flush()

        fmt = i_unpack(await conn._read(4))[0]
        blockSize = i_unpack(await conn._read(4))[0]
        conn.log.info(
            "Format=%d Block size=%d Host version=%d ", fmt, blockSize, hostversion
        )

        effectiveBlockSize = max(blockSize, 1)

        async def _send_chunk(data_chunk: bytes) -> None:
            # All server-sized blocks of one chunk go out in a single write and flush.
            data_len = len(data_chunk)
            frames = bytearray()
            for offset in range(0, data_len, effectiveBlockSize):
                block = data_chunk[offset:offset + effectiveBlockSize]
                frames.extend(i_pack(EXTAB_SOCK_DATA))
                frames.extend(i_pack(len(block)))
                frames.extend(block)
            if frames:
                await conn._write(frames)
                await conn._flush()
            conn.log.debug("No. of bytes sent to BE:%s", data_len)

        try:
            if filename.startswith(EXTERNAL_TABLE_STREAM_MARKER) and conn._ext_table_source is not None:
                conn.log.info("Using in-memory data source for external table import")
                source = conn._ext_table_source
                conn._ext_table_source = None

                if isinstance(source, (bytes, bytearray, memoryview)):
                    view = memoryview(source)
                    for offset in range(0, len(view), _SOURCE_PIECE_BYTES):
                        await _send_chunk(view[offset:offset + _SOURCE_PIECE_BYTES].tobytes())
                elif hasattr(source, "__aiter__"):
                    async for chunk in source:
                        if not chunk:
                            continue
                        await _send_chunk(bytes(chunk))
                else:
                    for chunk in source:
                        if not chunk:
                            continue
                        await _send_chunk(bytes(chunk))
            else:
                filehandle = await asyncio.to_thread(open, filename, "rb")
                conn.log.info(
                    "Successfully opened External file to read:%s", filename
                )
                try:
                    while True:
                        data = await asyncio.to_thread(filehandle.read, effectiveBlockSize)
                        if not data:
                            break
                        await _send_chunk(data)
                finally:
                    await asyncio.to_thread(filehandle.close)

            val = bytearray(i_pack(EXTAB_SOCK_DONE))
            await conn._write(val)
            await conn._flush()
            conn.log.info("sent EXTAB_SOCK_DONE to reader")

        except Exception as e:
            # Tell the server the stream is over, then let the protocol read the
            # server's answer to the statement (its error and ReadyForQuery). Raising
            # here would leave that answer unread and desynchronise the connection.
            conn.log.error("External table transfer for '%s' failed: %s", filename, str(e))
            conn._ext_table_failure = e
            try:
                # The server reads an error message and an error object after the
                # status code, mirroring the EXTAB_SOCK_ERROR frame it sends to us.
                message = str(e).encode("utf-8", "replace")[:_ERROR_FIELD_MAX]
                obj = filename.encode("utf-8", "replace")[:_ERROR_FIELD_MAX]
                val = bytearray(i_pack(EXTAB_SOCK_ERROR))
                val.extend(h_pack(len(message)))
                val.extend(message)
                val.extend(h_pack(len(obj)))
                val.extend(obj)
                await conn._write(val)
                await conn._flush()
            except Exception as exc:
                conn.log.debug(
                    "Error sending EXTAB_SOCK_ERROR after file failure: %s",
                    exc,
                    exc_info=True,
                )

    # ----- Log / error file retrieval --------------------------------------

    async def getFileFromBE(self, logDir: str, filename: str, logType: int) -> bool:
        """Retrieve a log / bad-row / stats file from the backend after an
        external table operation.
        """
        conn = self._conn
        status = True
        fullpath = os.path.join(logDir, filename)

        if logType == 1:
            fullpath = fullpath + ".nzlog"
            fh = await asyncio.to_thread(open, fullpath, "wb+")
        elif logType == 2:
            fullpath = fullpath + ".nzbad"
            fh = await asyncio.to_thread(open, fullpath, "wb+")
        elif logType == 3:
            fullpath = fullpath + ".nzstats"
            fh = await asyncio.to_thread(open, fullpath, "wb+")
        else:
            fh = await asyncio.to_thread(open, fullpath, "wb+")

        try:
            while True:
                numBytes = i_unpack(await conn._read(4))[0]
                if numBytes == 0:
                    break
                dataBuffer = await conn._read(numBytes)
                if status:
                    try:
                        await asyncio.to_thread(fh.write, dataBuffer)
                        conn.log.info(
                            "Successfully written data into file: %s", fullpath
                        )
                    except Exception as e:
                        conn.log.error(
                            "Error in writing data to file '%s': %s", fullpath, str(e)
                        )
                        status = False
        finally:
            await asyncio.to_thread(fh.close)

        return status


__all__ = [
    "ExternalTableManager",
]
