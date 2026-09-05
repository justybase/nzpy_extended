from __future__ import annotations

import logging
import socket
from collections.abc import Iterable, AsyncIterable
from typing import Any, Callable, Literal


from ._runner import runner  # pyright: ignore[reportPrivateUsage]
from .core import Connection
from .exceptions import (Warning, Error, InterfaceError, DatabaseError, DataError,
                         OperationalError, IntegrityError, InternalError,
                         ProgrammingError, NotSupportedError, ConnectionClosedError)
from .types import (Date, Time, Timestamp, DateFromTicks, TimeFromTicks,
                    TimestampFromTicks, Binary, BINARY, STRING, NUMBER, DATETIME, ROWID)

apilevel = "2.0"
threadsafety = 1
paramstyle = "qmark"

_log = logging.getLogger(__name__)


class SyncCursor:
    def __init__(self, async_cursor: Any) -> None:
        self._cursor: Any = async_cursor
        self._timeout: float | None = None

    @property
    def _c(self) -> Any:
        if self._cursor is None:
            raise InterfaceError("Cursor is closed")
        return self._cursor

    @property
    def timeout(self) -> float | None:
        return self._timeout

    @timeout.setter
    def timeout(self, value: float | None) -> None:
        self._timeout = value

    def execute(self, sql: str, args: Any | None = None, timeout: float | None = None) -> SyncCursor:
        if timeout is None:
            timeout = self._timeout
        runner.run(self._c.execute(sql, args, timeout=timeout))
        return self

    def executemany(self, sql: str, seq_of_args: list[Any]) -> SyncCursor:
        runner.run(self._c.executemany(sql, seq_of_args))
        return self

    def callproc(self, procname: str, parameters: list[Any] | None = None) -> list[Any] | None:
        return runner.run(self._c.callproc(procname, parameters))  # type: ignore[no-any-return]

    def fetchone(self) -> Any:
        return runner.run(self._c.fetchone())

    def fetchmany(self, num: int | None = None) -> list[Any]:
        if num is None:
            num = self.arraysize
        return runner.run(self._c.fetchmany(num))  # type: ignore[no-any-return]

    def fetchall(self) -> list[Any]:
        return runner.run(self._c.fetchall())  # type: ignore[no-any-return]

    def nextset(self) -> Any:
        return runner.run(self._c.nextset())

    @property
    def rowcount(self) -> int:
        return self._c.rowcount  # type: ignore[no-any-return]

    @property
    def description(self) -> Any:
        return self._c.description

    @property
    def statusmessage(self) -> Any:
        return getattr(self._c, 'statusmessage', None)

    @property
    def messages(self) -> Any:
        return self._c.messages

    def get_schema_table(self) -> Any:
        return self._c.get_schema_table()

    @property
    def rownumber(self) -> Any:
        return self._c.rownumber

    @property
    def arraysize(self) -> int:
        return self._c.arraysize  # type: ignore[no-any-return]

    @arraysize.setter
    def arraysize(self, value: int) -> None:
        self._c.arraysize = value

    def cancel(self, exec_gen: Any = None) -> None:
        runner.run(self._c.cancel(exec_gen))

    def interrupt(self) -> None:
        self.cancel()

    def close(self) -> None:
        try:
            if self._cursor is not None:
                runner.run(self._cursor.close())
        finally:
            self._cursor = None

    def __enter__(self) -> SyncCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        try:
            self.close()
        except Exception as exc:
            _log.debug("Error closing sync cursor: %s", exc, exc_info=True)

    def __iter__(self) -> Any:
        while True:
            rows = self.fetchmany()
            if not rows:
                break
            for row in rows:
                yield row

    def __repr__(self) -> str:
        return f"<{type(self).__name__} at 0x{id(self):x}>"

    def __del__(self) -> None:
        try:
            self._cursor = None
        except Exception:
            pass

    def setinputsizes(self, *args: Any) -> None:
        self._c.setinputsizes(*args)

    def setoutputsize(self, *args: Any) -> None:
        self._c.setoutputsize(*args)


class _TransactionContext:
    def __init__(self, conn: SyncConnection) -> None:
        self._conn = conn

    def __enter__(self) -> SyncConnection:
        if self._conn._conn is None:
            raise ConnectionClosedError()
        if self._conn._conn.in_transaction:
            raise NotSupportedError("Nested transaction contexts are not supported")
        self._autocommit = self._conn.autocommit
        runner.run(self._conn._conn._execute(self._conn._conn._cursor, "begin", None))
        self._conn._conn.in_transaction = True
        self._conn.autocommit = False
        return self._conn

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Literal[False]:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                try:
                    self._conn.rollback()
                except Exception:
                    _log.debug("Rollback failed during transaction exit", exc_info=True)
        finally:
            if not self._conn.closed:
                self._conn.autocommit = self._autocommit
        return False


class SyncConnection:
    def __init__(self, async_conn: Connection) -> None:
        self._conn: Connection | None = async_conn
        self._timeout: float | None = None

    @property
    def timeout(self) -> float | None:
        return self._timeout

    @timeout.setter
    def timeout(self, value: float | None) -> None:
        self._timeout = value

    @property
    def autocommit(self) -> bool:
        if self._conn is None:
            raise ConnectionClosedError()
        return self._conn.autocommit

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        if self._conn is None:
            raise ConnectionClosedError()
        self._conn.autocommit = value

    @property
    def closed(self) -> bool:
        return self._conn is None

    def cursor(self) -> SyncCursor:
        if self._conn is None:
            raise ConnectionClosedError()
        c = SyncCursor(self._conn.cursor())
        c._timeout = self._timeout  # pyright: ignore[reportPrivateUsage]
        return c

    def execute(self, sql: str, args: Any | None = None, timeout: float | None = None) -> SyncCursor:
        c = self.cursor()
        c.execute(sql, args, timeout=timeout)
        return c

    def commit(self) -> None:
        if self._conn is None:
            raise ConnectionClosedError()
        runner.run(self._conn.commit())

    def rollback(self) -> None:
        if self._conn is None:
            raise ConnectionClosedError()
        runner.run(self._conn.rollback())

    def cancel(self, exec_gen: Any = None) -> None:
        if self._conn is not None:
            runner.run(self._conn.cancel(exec_gen))

    def load_data(self, table_name: str, rows: Iterable[Any] | AsyncIterable[Any], columns: list[tuple[str, str]] | None = None,
                  delimiter: str = '|', encoding: str = 'LATIN9',
                  create_if_missing: bool = True, temporary: bool = False,
                  distribute_on_random: bool = True, logdir: str | None = None,
                  escape_char: str | None = '\\') -> int:
        if self._conn is None:
            raise ConnectionClosedError()
        return runner.run(self._conn.load_data(  # type: ignore[no-any-return]
            table_name=table_name,
            rows=rows,
            columns=columns,
            delimiter=delimiter,
            encoding=encoding,
            create_if_missing=create_if_missing,
            temporary=temporary,
            distribute_on_random=distribute_on_random,
            logdir=logdir,
            escape_char=escape_char,
        ))

    def load_csv(
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
        if self._conn is None:
            raise ConnectionClosedError()
        return runner.run(self._conn.load_csv(  # type: ignore[no-any-return]
            table_name=table_name,
            csv_path=csv_path,
            delimiter=delimiter,
            has_header=has_header,
            sample_size=sample_size,
            encoding=encoding,
            create_if_missing=create_if_missing,
            temporary=temporary,
            distribute_on_random=distribute_on_random,
            escape_char=escape_char,
            logdir=logdir,
        ))

    def transaction(self) -> _TransactionContext:
        return _TransactionContext(self)

    def close(self) -> None:
        try:
            if self._conn is not None:
                runner.run(self._conn.close())
        finally:
            self._conn = None

    def __repr__(self) -> str:
        status = "closed" if self._conn is None else "open"
        return f"<{type(self).__name__}({status}) at 0x{id(self):x}>"

    def __del__(self) -> None:
        try:
            if self._conn is not None:
                usock = getattr(self._conn, '_usock', None)
                if usock is not None:
                    try:
                        usock.shutdown(socket.SHUT_RDWR)
                    except OSError as e:
                        _log.warning("Socket shutdown error in __del__: %s", e)
                    try:
                        usock.close()
                    except OSError as e:
                        _log.warning("Socket close error in __del__: %s", e)
        except Exception as exc:
            _log.debug("Error in SyncConnection.__del__: %s", exc, exc_info=True)

    def __enter__(self) -> SyncConnection:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Literal[False]:
        try:
            if self._conn is not None:
                runner.run(self._conn.__aexit__(exc_type, exc_val, exc_tb))
        finally:
            self._conn = None
        return False


async def _async_connect(
    user: str | bytes,
    host: str | None = None,
    unix_sock: str | None = None,
    port: int = 5480,
    database: str | None = None,
    password: str | bytes | None = None,
    ssl: Any = None,
    securityLevel: int = 0,
    timeout: float | None = None,
    application_name: str | None = None,
    max_prepared_statements: int = 1000,
    datestyle: str = "ISO",
    logLevel: int = 0,
    tcp_keepalive: bool = True,
    char_varchar_encoding: str = "latin",
    client_encoding: str = "utf8",
    on_connect: Callable[[SyncConnection], Any] | None = None,
    ssl_verify: bool = True,
    connect_timeout: float | None = None,
    **kwargs: Any,
) -> Connection:
    conn = Connection()
    try:
        await conn.connect(
            user=user,
            host=host,
            unix_sock=unix_sock,
            port=port,
            database=database,
            password=password,
            ssl=ssl,
            securityLevel=securityLevel,
            timeout=timeout,
            application_name=application_name,
            max_prepared_statements=max_prepared_statements,
            datestyle=datestyle,
            logLevel=logLevel,
            tcp_keepalive=tcp_keepalive,
            char_varchar_encoding=char_varchar_encoding,
            client_encoding=client_encoding,
            ssl_verify=ssl_verify,
            connect_timeout=connect_timeout,
            **kwargs,
        )
        if on_connect is not None:
            result = on_connect(SyncConnection(conn))
            if hasattr(result, '__await__'):
                await result
    except Exception:
        try:
            await conn.close()
        except Exception:
            pass
        raise
    return conn


def connect(
    user: str,
    host: str = "localhost",
    unix_sock: str | None = None,
    port: int = 5480,
    database: str | None = None,
    password: str | None = None,
    ssl: Any = None,
    securityLevel: int = 0,
    timeout: float | None = None,
    application_name: str | None = None,
    max_prepared_statements: int = 1000,
    datestyle: str = "ISO",
    logLevel: int = 0,
    tcp_keepalive: bool = True,
    char_varchar_encoding: str = "latin",
    client_encoding: str = "utf8",
    on_connect: Callable[[SyncConnection], Any] | None = None,
    ssl_verify: bool = True,
    connect_timeout: float | None = None,
    **kwargs: Any,
) -> SyncConnection:
    async_conn = runner.run(
        _async_connect(
            user=user,
            host=host,
            unix_sock=unix_sock,
            port=port,
            database=database,
            password=password,
            ssl=ssl,
            securityLevel=securityLevel,
            timeout=timeout,
            application_name=application_name,
            max_prepared_statements=max_prepared_statements,
            datestyle=datestyle,
            logLevel=logLevel,
            tcp_keepalive=tcp_keepalive,
            char_varchar_encoding=char_varchar_encoding,
            client_encoding=client_encoding,
            on_connect=on_connect,
            ssl_verify=ssl_verify,
            connect_timeout=connect_timeout,
            **kwargs,
        )
    )
    return SyncConnection(async_conn)


def load_data(
    conn: SyncConnection,
    table_name: str,
    rows: list[Any],
    columns: list[tuple[str, str]] | None = None,
    delimiter: str = '|',
    encoding: str = 'LATIN9',
    create_if_missing: bool = True,
    temporary: bool = False,
    distribute_on_random: bool = True,
    logdir: str | None = None,
    escape_char: str | None = '\\',
) -> int:
    return conn.load_data(
        table_name=table_name,
        rows=rows,
        columns=columns,
        delimiter=delimiter,
        encoding=encoding,
        create_if_missing=create_if_missing,
        temporary=temporary,
        distribute_on_random=distribute_on_random,
        logdir=logdir,
        escape_char=escape_char,
    )


__all__ = ["SyncCursor", "SyncConnection", "connect", "load_data",
           "apilevel", "threadsafety", "paramstyle", "Warning", "Error",
           "InterfaceError", "DatabaseError", "DataError", "OperationalError",
           "IntegrityError", "InternalError", "ProgrammingError", "NotSupportedError",
           "Date", "Time", "Timestamp", "DateFromTicks", "TimeFromTicks",
           "TimestampFromTicks", "Binary", "BINARY", "STRING", "NUMBER", "DATETIME", "ROWID"]
