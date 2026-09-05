from __future__ import annotations

import tempfile
import getpass
import logging
import logging.handlers
import platform
import socket
import ssl as ssl_module
import asyncio
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, AsyncIterable
from copy import deepcopy
from datetime import (datetime as Datetime)


from typing import Any
from warnings import warn

import nzpy_extended

from . import handshake
from ._constants import DEFAULT_BUFFER_SIZE
from .buffered_stream import NzBufferedStream

from .exceptions import (Warning, Error, InterfaceError,
                         ConnectionClosedError, DatabaseError, DataError,
                         OperationalError, IntegrityError, InternalError,
                         ProgrammingError, NotSupportedError,
                         ArrayContentNotSupportedError,
                         ArrayContentNotHomogenousError,
                         ArrayDimensionsNotConsistentError)

from .types import (BINARY, Binary, Date, Time, Timestamp,
                    DateFromTicks, TimeFromTicks, TimestampFromTicks,
                    Interval, LogOptions,
                    PGType, PGEnum, PGJson, PGJsonb, PGText, PGTsvector,
                    PGVarchar,
                    FC_BINARY,
                    null_send)

from .protocol import (
    CONN_EXECUTING,
    EXTERNAL_TABLE_STREAM_MARKER,
    NULL_BYTE,
    nzpy_extended_client_version,
)

from .cursor import Cursor

from .load_data import load_data

from .utils import (
    i_pack, h_pack, q_pack, iii_pack, ii_pack,
    min_int2, max_int2, min_int4, max_int4,
    min_int8, max_int8,
    convert_paramstyle,
    render_prepared_statement,
    walk_array, array_find_first_element, array_flatten,
    array_check_dimensions, array_has_null,
    array_dim_lengths,
    pg_array_types,
    infer_columns_from_rows,
    rows_to_csv_bytes,
    rows_to_csv_chunks,
    pg_to_py_encodings,
)

arr_trans = dict(zip(map(ord, "[] 'u"), list('{}') + [None] * 3))


class Connection:
    sock: Any
    _usock: Any
    _stream: NzBufferedStream | None
    in_transaction: bool
    error: Any
    _ext_table_source: Any
    command_generation: int
    _char_varchar_encoding: str
    _client_encoding: str
    _commands_with_count: tuple[bytes, ...]
    notifications: deque[Any]
    parameter_statuses: deque[Any]
    max_prepared_statements: int
    log: logging.Logger
    user: bytes
    password: bytes | None
    autocommit: bool
    _caches: dict[str, Any]
    commandNumber: int
    status: int | None
    _host: str | None
    _port: int
    _unix_sock: str | None
    _backend_pid: int | None
    _backend_key: int | None
    _read: Callable[[int], Any]
    _write: Callable[[bytes | bytearray], Any]
    _flush: Callable[[], Any]
    _backend_key_data: Any
    _dirty_socket: bool
    pg_types: defaultdict[Any, Any]
    py_types: dict[Any, Any] = {}
    inspect_funcs: dict[Any, Any] = {}
    message_types: dict[Any, Any]
    _cursor: Cursor
    _active_generator: Any
    _active_cursor: Any
    _cached_header: Any
    _copy_done: bool
    _server_version: Any
    tupdesc: Any

    Warning = property(lambda self: self._getError(Warning))
    Error = property(lambda self: self._getError(Error))
    InterfaceError = property(lambda self: self._getError(InterfaceError))
    ConnectionClosedError = property(lambda self:
                                     self._getError(ConnectionClosedError))
    DatabaseError = property(lambda self: self._getError(DatabaseError))
    OperationalError = property(lambda self: self._getError(OperationalError))
    IntegrityError = property(lambda self: self._getError(IntegrityError))
    InternalError = property(lambda self: self._getError(InternalError))
    ProgrammingError = property(lambda self: self._getError(ProgrammingError))
    NotSupportedError = property(
        lambda self: self._getError(NotSupportedError))

    async def __aenter__(self) -> Connection:
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        try:
            if exc_type is None:
                await self.commit()
            else:
                try:
                    await self.rollback()
                except Exception:
                    self.log.debug("Rollback failed during context exit", exc_info=True)
        finally:
            try:
                await self.close()
            except Exception:
                self.log.debug("Close failed during context exit", exc_info=True)

    def __del__(self) -> None:
        try:
            usock = getattr(self, '_usock', None)
            if usock is not None:
                log = getattr(self, 'log', logging.getLogger("nzpy_extended"))
                try:
                    usock.shutdown(socket.SHUT_RDWR)
                except Exception as exc:
                    log.debug(
                        "Socket shutdown error in Connection.__del__: %s",
                        exc,
                        exc_info=True,
                    )
                try:
                    usock.close()
                except Exception as exc:
                    log.debug(
                        "Socket close error in Connection.__del__: %s",
                        exc,
                        exc_info=True,
                    )
        except Exception as exc:
            logging.getLogger("nzpy_extended").debug(
                "Error in Connection.__del__: %s", exc, exc_info=True
            )

    def _getError(self, error: type) -> type:
        warn(
            "DB-API extension connection.%s used" %
            error.__name__, stacklevel=3)
        return error

    def __init__(self) -> None:
        self._closed = False
        self.sock = None
        self._usock = None
        self._stream = None
        self.in_transaction = False
        self.error = None
        self._ext_table_source = None
        self.command_generation = 0
        self._buffer_size = DEFAULT_BUFFER_SIZE

        # Internal service objects — created here so they're available
        # immediately after ``Connection()``, even before ``connect()``.
        from ._protocol import ProtocolHandler
        from ._dbos import DbosParser
        from ._extab import ExternalTableManager
        from ._metadata import MetadataResolver
        from ._metadata_api import ConnectionMetadataProvider

        self._protocol = ProtocolHandler(self)
        self._dbos = DbosParser(self)
        self._extab = ExternalTableManager(self)
        self._meta = MetadataResolver()
        self.meta = ConnectionMetadataProvider(self)

    async def connect(
            self, user: str | bytes, host: str | None, unix_sock: str | None, port: int,
            database: str | None, password: str | bytes | None, ssl: Any,
            securityLevel: int, timeout: float | None, application_name: str | None,
            max_prepared_statements: int, datestyle: str, logLevel: int, tcp_keepalive: bool,
            char_varchar_encoding: str, logOptions: LogOptions = LogOptions.Inherit,
            client_encoding: str = "utf8",
            pgOptions: str | None = None, ssl_verify: bool = True,
            connect_timeout: float | None = None,
            buffer_size: int = DEFAULT_BUFFER_SIZE) -> None:
        self._buffer_size = buffer_size
        self._char_varchar_encoding = char_varchar_encoding
        self._client_encoding = "utf8"
        self._commands_with_count = (
            b"INSERT", b"DELETE", b"UPDATE"
        )
        self.notifications = deque(maxlen=100)
        self.parameter_statuses = deque(maxlen=100)
        self.max_prepared_statements = int(max_prepared_statements)

        if logLevel not in (logging.DEBUG, logging.ERROR,
                            logging.CRITICAL, logging.FATAL,
                            logging.WARN, logging.WARNING):
            if logLevel == 0:
                logLevel = logging.DEBUG
            elif logLevel == 1:
                logLevel = logging.INFO
            elif logLevel == 2:
                logLevel = logging.WARNING
            else:
                logLevel = logging.INFO

        database_name = database if database is not None else "<default>"
        self.log = logging.getLogger(f"nzpy_extended.Connection[{database_name}]")
        self.log.setLevel(logLevel)

        if logOptions & LogOptions.Logfile:
            h = logging.handlers. \
                RotatingFileHandler('nzpy_extended.log', maxBytes=1024 ** 3 * 10)
            fmt = logging.Formatter(
                '%(asctime)s (%(process)s) [%(name)s:%(filename)s:'
                '%(lineno)s] %(levelname)s: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S.000000 %Z')
            h.setFormatter(fmt)
            self.log.addHandler(h)
        if not logOptions & LogOptions.Inherit:
            self.log.propagate = False

        if user is None:
            raise InterfaceError(
                "The 'user' connection parameter cannot be None")

        if isinstance(user, str):
            self.user = user.encode('utf8')
        else:
            self.user = user

        if isinstance(password, str):
            self.password = password.encode('utf8')
        else:
            self.password = password

        self.autocommit = True

        self._caches = {}
        self.commandNumber = -1
        self.status = None
        self._host = host
        self._port = port
        self._unix_sock = unix_sock

        try:
            if unix_sock is None and host is not None:
                self._usock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            elif unix_sock is not None:
                if not hasattr(socket, "AF_UNIX"):  # pyright: ignore[reportAttributeAccessIssue]
                    raise InterfaceError(
                        "attempt to connect to unix socket on unsupported "
                        "platform")
                self._usock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType,reportUnknownArgumentType]
            else:
                raise ProgrammingError(
                    "one of host or unix_sock must be provided")
            sock_timeout = connect_timeout if connect_timeout is not None else timeout

            self._usock.setblocking(False)
            loop = asyncio.get_event_loop()
            if unix_sock is None and host is not None:
                connect_coro = loop.sock_connect(self._usock, (host, port))
            elif unix_sock is not None:
                connect_coro = loop.sock_connect(self._usock, unix_sock)
            else:
                connect_coro = None
            if sock_timeout is not None and connect_coro is not None:
                await asyncio.wait_for(connect_coro, timeout=sock_timeout)
            elif connect_coro is not None:
                await connect_coro

            if tcp_keepalive:
                self._usock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except socket.error as e:
            if self._usock is not None:
                self._usock.close()
                self._usock = None
            raise InterfaceError("communication error", e) from e

        self._usock.setblocking(True)
        if not isinstance(ssl, dict):
            hs_ssl: dict[str, Any] = {}
        else:
            hs_ssl = dict(ssl)  # pyright: ignore[reportUnknownArgumentType]
        hs_ssl.setdefault('ssl_verify', ssl_verify)
        hs_ssl.setdefault('server_hostname', host)
        hs = handshake.SyncHandshake(self._usock, hs_ssl, self.log)
        if application_name:
            hs.guardium_applName = application_name
        try:
            result = await asyncio.to_thread(
                hs.startup, database, securityLevel, user, password, pgOptions)
            if result is False:
                raise ProgrammingError(hs.last_error or "Error in handshake")
            self._usock = result
        except BaseException:
            failed_socket = getattr(hs, '_usock', self._usock)
            try:
                failed_socket.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                failed_socket.close()
                failed_file = getattr(hs, '_sock', None)
                if failed_file is not None:
                    failed_file.close()
            except Exception:
                self.log.debug("Error cleaning up failed handshake", exc_info=True)
            self._usock = None
            raise
        self._backend_pid = hs.backend_pid
        self._backend_key = hs.backend_key
        self._usock.setblocking(False)

        self._stream = NzBufferedStream(self._usock, max_size=self._buffer_size,
                                        buffer_size=self._buffer_size,
                                        on_fatal=self.close)

        stream = self._stream
        assert stream is not None

        async def _read(n: int) -> bytes:
            return await stream.read(n)

        async def _write(data: bytes | bytearray) -> None:
            await stream.write(data)

        async def _flush() -> None:
            pass

        self._read = _read
        self._write = _write
        self._flush = _flush
        self._backend_key_data = None
        self._dirty_socket = False

        if hs.server_client_encoding is not None:
            encoding_lower = hs.server_client_encoding
        else:
            encoding_lower = client_encoding.lower()
        self._client_encoding = pg_to_py_encodings.get(encoding_lower) or encoding_lower

        from ._serializers import build_pg_types, build_py_types

        self.pg_types = build_pg_types(self._client_encoding)
        self.py_types = build_py_types(self._client_encoding)

        self.inspect_funcs = {
            Datetime: self.inspect_datetime,
            list: self.array_inspect,
            tuple: self.array_inspect,
            int: self.inspect_int}

        self.message_types = self._protocol.message_types

        async def conn_send_query() -> bool:

            if not await self._execute(self._cursor, "set nz_encoding to "
                                               f"'{client_encoding}'", None):
                return False

            if datestyle == 'MDY':
                query = "set DateStyle to 'US'"
            elif datestyle == 'DMY':
                query = "set DateStyle to 'EUROPEAN'"
            else:
                query = "set DateStyle to 'ISO'"

            if not await self._execute(self._cursor, query, None):
                return False

            client_info = "select version(), 'Netezza Python " \
                          "Client Version: {}', " \
                          "'{}', 'OS Platform: {}', 'OS Username: {}'"

            query = client_info.format(nzpy_extended_client_version,
                                       platform.uname().machine,
                                       platform.system(),
                                       getpass.getuser())

            if not await self._execute(self._cursor, query, None):
                return False
            else:
                results = await self._cursor.fetchall()
                for c1, c2, c3, c4, c5 in results:
                    self.log.debug("c1 = %s, c2 = %s, c3 = %s, c4 = %s, "
                                   "c5 = %s" % (c1, c2, c3, c4, c5))

            client_info = "SET CLIENT_VERSION = '{}'"
            query = client_info.format(nzpy_extended_client_version)
            if not await self._execute(self._cursor, query, None):
                return False

            if not await self._execute(self._cursor, "select ascii(' ') as space, "
                                               "encoding as ccsid "
                                               "from _v_database "
                                               "where objid = current_db",
                                  None):
                return False
            else:
                results = await self._cursor.fetchall()
                for c1, c2 in results:
                    self.log.debug("c1 = %s, c2 = %s" % (c1, c2))

            if not await self._execute(self._cursor, "select feature from "
                                               "_v_odbc_feature "
                                               "where spec_level = '3.5'",
                                  None):
                return False
            else:
                results = await self._cursor.fetchall()
                for c1 in results:
                    self.log.debug("c1 = %s" % (c1))

            if not await self._execute(self._cursor, "select identifier_case, "
                                               "current_catalog, "
                                               "current_user", None):
                return False
            else:
                results = await self._cursor.fetchall()
                for c1, c2, c3 in results:
                    self.log.debug("c1 = %s, c2 = %s, c3 = %s" % (c1, c2, c3))

            return True

        self._cursor = self.cursor()
        self.error = None

        if not await conn_send_query():
            self.log.warning("Error sending initial setup queries")

        self.commandNumber = 0

        if self.error is not None:
            raise self.error

        self.in_transaction = False
        await self._read(4)
        self.status = None

    # (handle_* methods moved to _protocol.py)

    def cursor(self) -> Cursor:
        if self._closed:
            raise ConnectionClosedError()
        return Cursor(self)

    async def execute(self, operation: str, args: Any | None = None, timeout: float | None = None) -> Cursor:
        c = self.cursor()
        await c.execute(operation, args, timeout=timeout)
        return c

    async def commit(self) -> None:
        if self._closed:
            raise ConnectionClosedError()
        await self._execute(self._cursor, "commit", None)
        self.in_transaction = False

    async def rollback(self) -> None:
        if self._closed:
            raise ConnectionClosedError()
        if not self.in_transaction:
            return
        await self._execute(self._cursor, "rollback", None)
        self.in_transaction = False

    async def close(self) -> None:
        self._closed = True
        if getattr(self, '_usock', None) is None:
            return

        try:
            if getattr(self, '_usock', None) is not None:
                try:
                    self._usock.shutdown(socket.SHUT_RDWR)
                except OSError as e:
                    self.log.warning("Socket shutdown error during close: %s", e)
                try:
                    self._usock.close()
                except OSError as e:
                    self.log.warning("Socket close error during close: %s", e)
                self._usock = None
        except Exception as exc:
            self.log.debug("Error during connection close cleanup: %s", exc, exc_info=True)

        if hasattr(self, '_stream') and self._stream is not None:
            try:
                self._stream.close()
            except OSError as e:
                self.log.warning("Stream close error during close: %s", e)
            self._stream = None

        self.sock = None

    async def cancel(self, exec_gen: int | None = None) -> None:
        if getattr(self, '_backend_pid', None) is None or getattr(self, '_backend_key', None) is None:
            return

        if exec_gen is not None and exec_gen != self.command_generation:
            return

        try:
            if getattr(self, '_unix_sock', None) is not None:
                open_unix = getattr(asyncio, "open_unix_connection", None)
                if open_unix is None:
                    return
                reader, writer = await open_unix(self._unix_sock)
            else:
                if getattr(self, '_host', None) is None:
                    return
                reader, writer = await asyncio.open_connection(self._host, self._port)

            if exec_gen is not None and exec_gen != self.command_generation:
                writer.close()  # pyright: ignore[reportUnknownMemberType]
                return

            cancel_code = 80877102
            msg = bytearray(i_pack(16) + i_pack(cancel_code) + i_pack(self._backend_pid) + i_pack(self._backend_key))
            writer.write(msg)  # pyright: ignore[reportUnknownMemberType]
            await writer.drain()  # pyright: ignore[reportUnknownMemberType]
            try:
                await reader.read(1)  # pyright: ignore[reportUnknownMemberType]
            except Exception as exc:
                self.log.debug(
                    "Error reading cancel response: %s", exc, exc_info=True
                )
            writer.close()  # pyright: ignore[reportUnknownMemberType]
            await writer.wait_closed()  # pyright: ignore[reportUnknownMemberType]
            self.log.info("Sent cancellation request to backend")
        except Exception as e:
            self.log.warning("Could not send cancel request: %s", str(e))

    async def load_data(self, table_name: str, rows: Iterable[Any] | AsyncIterable[Any], columns: list[tuple[str, str]] | None = None,
                        delimiter: str = '|', encoding: str = 'LATIN9',
                        create_if_missing: bool = True, temporary: bool = False,
                        distribute_on_random: bool = True, logdir: str | None = None,
                        escape_char: str | None = '\\') -> int:
        if logdir is None:
            logdir = tempfile.gettempdir()

        if create_if_missing and columns is None:
            if isinstance(rows, AsyncIterable):
                rows_iter = rows.__aiter__()
                try:
                    first_row = await rows_iter.__anext__()
                except StopAsyncIteration:
                    raise ProgrammingError("No rows to load") from None
                remaining: list[Any] = []
                async for row in rows_iter:
                    remaining.append(row)
            else:
                sync_iter = iter(rows)
                try:
                    first_row = next(sync_iter)
                except StopIteration:
                    raise ProgrammingError("No rows to load") from None
                remaining = list(sync_iter)
            all_rows = [first_row] + remaining
            columns = infer_columns_from_rows(all_rows)
            rows = all_rows

        if create_if_missing and columns:
            col_defs = ', '.join(f'{name} {nz_type}'
                                 for name, nz_type in columns)
            parts = ['CREATE']
            if temporary:
                parts.append('TEMP')
            parts.append(f'TABLE IF NOT EXISTS {table_name} ({col_defs})')
            if distribute_on_random:
                parts.append('DISTRIBUTE ON RANDOM')
            ddl = ' '.join(parts)
            cur = self.cursor()
            await cur.execute(ddl)

        if columns is not None and any(t.startswith('NVARCHAR') or t.startswith('NCLOB') for _, t in columns):
            encoding = 'UTF8'

        if isinstance(rows, (list, tuple)):
            csv_bytes = rows_to_csv_bytes(rows, delimiter, encoding, escape_char,
                                           columns=columns)
            self._ext_table_source = csv_bytes
        else:
            self._ext_table_source = rows_to_csv_chunks(rows, delimiter, encoding, escape_char,
                                                         columns=columns)

        using_opts = f"ENCODING '{encoding}' REMOTESOURCE 'python' DELIMITER '{delimiter}'"
        if escape_char is not None:
            using_opts += f" ESCAPECHAR '{escape_char}'"
        using_opts += f" LOGDIR '{logdir}'"

        if columns is not None and any(t == 'BOOLEAN' for _, t in columns):
            using_opts += " BOOLSTYLE '1_0'"

        sql = (
            f"INSERT INTO {table_name} SELECT * "
            f"FROM EXTERNAL '{EXTERNAL_TABLE_STREAM_MARKER}' "
            f"SAMEAS {table_name} "
            f"USING ({using_opts})"
        )
        cur = self.cursor()
        await cur.execute(sql)
        return cur.rowcount

    async def load_csv(
        self,
        table_name: str,
        csv_path: str,
        delimiter: str = ',',
        has_header: bool = True,
        sample_size: int = 1000,
        encoding: str = 'UTF8',
        create_if_missing: bool = True,
        temporary: bool = False,
        distribute_on_random: bool = True,
        escape_char: str | None = '\\',
        logdir: str | None = None,
    ) -> int:
        from .csv_import import load_csv as _load_csv

        return await _load_csv(
            self, table_name, csv_path,
            delimiter=delimiter,
            has_header=has_header,
            sample_size=sample_size,
            encoding=encoding,
            create_if_missing=create_if_missing,
            temporary=temporary,
            distribute_on_random=distribute_on_random,
            escape_char=escape_char,
            logdir=logdir,
        )

    def inspect_datetime(self, value: Datetime) -> Any:
        if value.tzinfo is None:
            return self.py_types[1114]
        else:
            return self.py_types[1184]

    def inspect_int(self, value: int) -> Any | None:
        if min_int2 < value < max_int2:
            return self.py_types[21]
        if min_int4 < value < max_int4:
            return self.py_types[23]
        if min_int8 < value < max_int8:
            return self.py_types[20]
        return None

    def make_params(self, values: tuple[Any, ...]) -> tuple[Any, ...]:
        params: list[Any] = []
        for value in values:
            typ = type(value)  # pyright: ignore[reportUnknownVariableType]
            try:
                params.append(self.py_types[typ])
            except KeyError:
                try:
                    params.append(self.inspect_funcs[typ](value))
                except KeyError as e:
                    param = None
                    for k, v in self.py_types.items():
                        try:
                            if isinstance(value, k):
                                param = v
                                break
                        except TypeError:
                            pass

                    if param is None:
                        for k, v in self.inspect_funcs.items():
                            try:
                                if isinstance(value, k):
                                    param = v(value)
                                    break
                            except TypeError:
                                pass
                            except KeyError:
                                pass

                    if param is None:
                        raise NotSupportedError(
                            "type " + str(e) + " not mapped to pg type")
                    else:
                        params.append(param)

        return tuple(params)

    async def Prepare(self, cursor: Cursor, query: str, vals: Any) -> str:
        statement, make_args = convert_paramstyle(nzpy_extended.paramstyle, query)
        args = tuple(make_args(vals))
        if len(args) >= 65536:
            self.log.warning("got %d parameters but PostgreSQL only "
                             "supports 65535 parameters", len(args))
        rendered_query, expected_args = render_prepared_statement(statement, args)
        if expected_args == 0:
            return statement
        if len(args) != expected_args:
            self.log.warning("got %d parameters but the statement requires %d", len(args), expected_args)
        return rendered_query

    async def drain_protocol_generator(self, generator: Any) -> None:
        if generator is None:
            return
        try:
            async for _state in generator:
                pass
        except StopAsyncIteration:
            pass
        finally:
            self._active_generator = None

    def _peek_socket_bytes(self) -> bytes:
        """Non-blocking peek of bytes sitting on the OS socket (not yet buffered)."""
        usock = getattr(self, '_usock', None)
        if usock is None:
            return b''
        if isinstance(usock, ssl_module.SSLSocket):
            return self._stream.peek_tls() if self._stream is not None else b''
        try:
            return bytes(usock.recv(65536, socket.MSG_PEEK))
        except (BlockingIOError, InterruptedError):
            return b''
        except OSError:
            return b''

    def _has_unread_non_null_bytes(self) -> bool:
        """True when buffered or socket data contains a non-null protocol byte."""
        stream = getattr(self, '_stream', None)
        if stream is not None and stream.has_buffered_non_null():
            return True
        peeked = self._peek_socket_bytes()
        for b in peeked:
            if b != 0:
                return True
        return False

    async def _execute(self, cursor: Cursor, query: str, vals: Any) -> str | None:
        active_gen = getattr(self, '_active_generator', None)
        if active_gen is not None:
            old_cursor = getattr(self, '_active_cursor', None)
            await self.drain_protocol_generator(active_gen)
            if old_cursor is not None:
                old_cursor.cached_rows.clear()
                old_cursor.generator = None
                self._active_cursor = None
        else:
            stale_cursor = getattr(self, '_active_cursor', None)
            if stale_cursor is not None:
                stale_cursor.cached_rows.clear()
                stale_cursor.generator = None
                self._active_cursor = None

        if getattr(self, '_dirty_socket', False) or self._has_unread_non_null_bytes():
            await self._protocol._drain_socket()

        self._dirty_socket = True

        self.command_generation += 1
        self.error = None
        cursor.notices = deque()
        cursor.row_count = -1
        cursor.has_rows = False
        cursor.ps = {'row_desc': []}

        if vals is None:
            vals = ()
        else:
            query = await self.Prepare(cursor, query, vals)

        if self.status == CONN_EXECUTING:
            await self._read(4)

        buf = bytearray(b'P\xFF\xFF\xFF\xFF')

        if self.commandNumber != -1:
            self.commandNumber += 1
            buf = bytearray(b'P' + i_pack(self.commandNumber))

        if self.commandNumber > 100000:
            self.commandNumber = 1

        query_bytes = query.encode('utf8')
        buf.extend(query_bytes + NULL_BYTE)
        await self._write(buf)
        await self._flush()

        self.log.debug("Buffer sent to nps:%s", buf)

        self.status = CONN_EXECUTING

        cursor.generator = self._protocol._connNextResultSetGenerator(cursor)
        self._active_generator = cursor.generator
        self._active_cursor = cursor
        response = None
        try:
            while True:
                try:
                    state = await cursor.generator.__anext__()
                except StopAsyncIteration:
                    break
                if state == "ROW_DESCRIPTION":
                    response = state
                    if (cursor.ps.get('tupdesc') is None and
                            len(cursor.ps.get('row_desc', [])) > 0):
                        continue
                    break
                if state in ("DATA_ROW", "DATA_BATCH"):
                    response = "ROW_DESCRIPTION"
                    break
                if state == "COMMAND_COMPLETE":
                    response = "COMMAND_COMPLETE"
                    continue
                if state == "READY_FOR_QUERY":
                    response = "READY_FOR_QUERY"
                    break
                if state == "ERROR":
                    response = state
                    await self.drain_protocol_generator(cursor.generator)
                    cursor.generator = None
                    break
        except StopAsyncIteration:
            pass

        if self.error is not None:
            raise self.error

        if response == "ROW_DESCRIPTION" and len(cursor.ps.get('row_desc', [])) > 0:
            cursor.has_rows = True
        else:
            cursor.has_rows = len(cursor.cached_rows) > 0
        return response

    # (methods moved to _protocol.py, _dbos.py, _extab.py, _metadata.py)

    def array_inspect(self, value: list[Any]) -> Any:
        first_element = array_find_first_element(value)
        oid: int = 25
        fc: int = FC_BINARY
        array_oid: int = 0
        send_func: Any = null_send
        typ: Any = str
        if first_element is None:
            oid = 25
            fc = FC_BINARY
            array_oid = pg_array_types[oid]
        else:
            typ = type(first_element)

            if issubclass(typ, int):
                typ = int
                int2_ok, int4_ok, int8_ok = True, True, True
                for v in array_flatten(value):
                    if v is None:
                        continue
                    if min_int2 < v < max_int2:  # type: ignore[operator]
                        continue
                    int2_ok = False
                    if min_int4 < v < max_int4:  # type: ignore[operator]
                        continue
                    int4_ok = False
                    if min_int8 < v < max_int8:  # type: ignore[operator]
                        continue
                    int8_ok = False
                if int2_ok:
                    array_oid = 1005
                    oid, fc, send_func = (21, FC_BINARY, h_pack)
                elif int4_ok:
                    array_oid = 1007
                    oid, fc, send_func = (23, FC_BINARY, i_pack)
                elif int8_ok:
                    array_oid = 1016
                    oid, fc, send_func = (20, FC_BINARY, q_pack)
                else:
                    raise ArrayContentNotSupportedError(
                        "numeric not supported as array contents")
            else:
                try:
                    oid, fc, send_func = self.make_params((first_element,))[0]

                    if oid in (705, 1043, 25):
                        oid = 25
                        fc = FC_BINARY
                    array_oid = pg_array_types[oid]
                except KeyError:
                    raise ArrayContentNotSupportedError(
                        "oid " + str(oid) + " not supported as array contents")
                except NotSupportedError:
                    raise ArrayContentNotSupportedError(
                        "type " + str(typ) +
                        " not supported as array contents")
        if fc == FC_BINARY:
            def send_array_binary(arr: list[Any]) -> bytearray:
                array_check_dimensions(arr)

                has_null = array_has_null(arr)
                dim_lengths = array_dim_lengths(arr)
                data = bytearray(iii_pack(len(dim_lengths), has_null, oid))
                for i in dim_lengths:
                    data.extend(ii_pack(i, 1))
                for v in array_flatten(arr):
                    if v is None:
                        data += i_pack(-1)
                    elif isinstance(v, typ):
                        inner_data = send_func(v)
                        data += i_pack(len(inner_data))
                        data += inner_data
                    else:
                        raise ArrayContentNotHomogenousError(
                            "not all array elements are of type " + str(typ))
                return data
        else:
            def send_array_text(arr: list[Any]) -> bytes:
                array_check_dimensions(arr)
                ar = deepcopy(arr)
                for a, i, v in walk_array(ar):
                    if v is None:
                        a[i] = 'NULL'
                    elif isinstance(v, typ):
                        a[i] = send_func(v).decode('ascii')
                    else:
                        raise ArrayContentNotHomogenousError(
                            "not all array elements are of type " + str(typ))
                return str(ar).translate(arr_trans).encode('ascii')

        return (array_oid, fc, send_array_binary if fc == FC_BINARY else send_array_text)


def __getattr__(name: str) -> Any:
    """Dynamic re-exports for C extension state so that monkeypatching
    ``_cstate._HAVE_C_EXT`` is always reflected here.
    """
    if name in ("_HAVE_C_EXT", "_c_ext"):
        from . import _cstate
        return getattr(_cstate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Connection",
    "Warning", "Error", "InterfaceError", "ConnectionClosedError",
    "DatabaseError", "DataError", "OperationalError", "IntegrityError",
    "InternalError", "ProgrammingError", "NotSupportedError",
    "ArrayContentNotSupportedError", "ArrayContentNotHomogenousError",
    "ArrayDimensionsNotConsistentError",
    "BINARY", "Binary", "Date", "Time", "Timestamp",
    "DateFromTicks", "TimeFromTicks", "TimestampFromTicks",
    "Interval", "LogOptions",
    "PGType", "PGEnum", "PGJson", "PGJsonb", "PGText", "PGTsvector",
    "PGVarchar",
    "Cursor",
    "load_data",
]
