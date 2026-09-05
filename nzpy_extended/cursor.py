from __future__ import annotations
import asyncio
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from warnings import warn

from .exceptions import (ConnectionClosedError, InterfaceError,
                         OperationalError, ProgrammingError)

if TYPE_CHECKING:
    from .core import Connection


class Cursor:
    __module__ = 'nzpy_extended.core'

    def __init__(self, connection: Connection) -> None:
        self._c: Connection | None = connection
        self.arraysize = 100
        self.ps: dict[str, Any] | None = None
        self.row_count = -1
        self.cached_rows: deque[Any] = deque()
        self.notices: deque[Any] = deque()
        self.generator: Any = None
        self.has_rows = False
        self._rownumber = 0
        self.stream: Any = None
        self._timeout: float | None = None
        self._exec_gen: int | None = None
        self.notice_handler: Callable[[str], Any] | None = None

    async def __aenter__(self) -> Cursor:
        return self

    def _ensure_open(self) -> None:
        if self._c is None:
            raise InterfaceError("Cursor is closed")
        if self._c._closed:
            raise ConnectionClosedError()
        if self._c._stream is not None and self._c._stream.view is None:
            raise ConnectionClosedError()

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.close()

    @property
    def connection(self) -> Connection | None:
        warn("DB-API extension cursor.connection used", stacklevel=3)
        return self._c

    @property
    def messages(self) -> deque[Any]:
        return self.notices

    @property
    def rownumber(self) -> int:
        return self._rownumber

    @property
    def rowcount(self) -> int:
        return self.row_count

    description = property(lambda self: self._getDescription())

    def _getDescription(self) -> tuple[Any, ...] | None:
        if self.ps is None:
            return None
        row_desc = self.ps['row_desc']
        if len(row_desc) == 0:
            return None
        tupdesc = self.ps.get('tupdesc')
        columns: list[tuple[Any, ...]] = []
        for i, col in enumerate(row_desc):
            meta =         self._c._meta.resolve_column_metadata(col, i, tupdesc) if self._c else None
            if meta is None:
                columns.append((col["name"].decode(), col["type_oid"],
                                None, None, None, None, None))
            else:
                columns.append((
                    meta['name'],
                    meta['provider_type'],
                    meta['display_size'],
                    meta['internal_size'],
                    meta['numeric_precision'] if meta['numeric_precision'] >= 0 else None,
                    meta['numeric_scale'] if meta['numeric_scale'] >= 0 else None,
                    meta['null_ok'],
                ))
        return tuple(columns)

    def get_schema_table(self) -> list[dict[str, Any]]:
        if self.ps is None:
            return []
        row_desc = self.ps['row_desc']
        if len(row_desc) == 0:
            return []
        tupdesc = self.ps.get('tupdesc')
        rows: list[dict[str, Any]] = []
        for i, col in enumerate(row_desc):
            meta = self._c._meta.resolve_column_metadata(col, i, tupdesc) if self._c else None
            if meta is None:
                continue
            rows.append({
                'ColumnName': meta['name'],
                'ColumnOrdinal': i + 1,
                'ColumnSize': meta['column_size'],
                'NumericPrecision': meta['numeric_precision'],
                'NumericScale': meta['numeric_scale'],
                'DataType': meta['data_type'],
                'ProviderType': meta['provider_type'],
                'AllowDBNull': meta['null_ok'],
                'IsReadOnly': True,
                'IsLong': meta['is_long'],
                'IsAutoIncrement': False,
            })
        return rows

    def get_column_metadata(self, index: int) -> dict[str, Any] | None:
        if self.ps is None or index < 0 or index >= len(self.ps['row_desc']):
            raise ProgrammingError(f"Column ordinal {index} is out of range")
        col = self.ps['row_desc'][index]
        tupdesc = self.ps.get('tupdesc')
        if self._c is None:
            raise ProgrammingError("Cursor closed")
        return         self._c._meta.resolve_column_metadata(col, index, tupdesc)

    async def execute(self, operation: str, args: Any | None = None, stream: Any = None, timeout: float | None = None) -> Cursor:
        self._ensure_open()
        try:
            self.stream = stream
            self._timeout = timeout
            await self.clear()

            if self._c is not None and not self._c.in_transaction and not self._c.autocommit:
                await self._c._execute(self, "begin", None)
                self._c.in_transaction = True

            if self._c is not None:
                exec_gen = self._c.command_generation + 1
                self._exec_gen = exec_gen
                coro = self._c._execute(self, operation, args)
                if timeout is not None and timeout > 0:
                    try:
                        await asyncio.wait_for(coro, timeout=timeout)
                    except asyncio.TimeoutError:
                        await self._c.cancel(exec_gen=exec_gen)
                        raise OperationalError("Command execution timeout")
                else:
                    await coro

        except AttributeError as e:
            if self._c is None:
                raise InterfaceError("Cursor closed")
            elif self._c.sock is None:
                raise ConnectionClosedError()
            else:
                raise e
        return self

    async def executemany(self, operation: str, param_sets: list[Any]) -> Cursor:
        await self.clear()
        rowcounts: list[int] = []
        for i, parameters in enumerate(param_sets):
            try:
                await self.execute(operation, parameters)
            except Exception as e:
                self.row_count = -1 if -1 in rowcounts else sum(rowcounts)
                raise ProgrammingError(
                    f"executemany failed at param set {i + 1}/{len(param_sets)}: {e}"
                ) from e
            rowcounts.append(self.row_count)

        self.row_count = -1 if -1 in rowcounts else sum(rowcounts)
        return self

    async def callproc(self, procname: str, parameters: list[Any] | None = None) -> list[Any] | None:
        if parameters:
            placeholders = ', '.join(['?'] * len(parameters))
            sql = f"CALL {procname}({placeholders})"
            await self.execute(sql, parameters)
            return list(parameters)
        sql = f"CALL {procname}()"
        await self.execute(sql)
        return None

    async def fetchone(self) -> Any:
        try:
            return await self.__anext__()
        except StopAsyncIteration:
            return None
        except TypeError:
            raise ProgrammingError("attempting to use unexecuted cursor")
        except AttributeError:
            raise ProgrammingError("attempting to use unexecuted cursor")

    async def fetchmany(self, num: int | None = None) -> list[Any]:
        self._ensure_open()
        try:
            rows: list[Any] = []
            for _ in range(self.arraysize if num is None else num):
                try:
                    rows.append(await self.__anext__())
                except StopAsyncIteration:
                    break
            return rows
        except TypeError:
            raise ProgrammingError("attempting to use unexecuted cursor")

    async def fetchall(self) -> list[Any]:
        self._ensure_open()
        try:
            generator = getattr(self, 'generator', None)
            if generator is None:
                if self.ps is None:
                    raise ProgrammingError("A query hasn't been issued.")
                elif len(self.ps['row_desc']) == 0:
                    raise ProgrammingError("no result set")
                return []
            rows = list(self.cached_rows)
            self.cached_rows.clear()
            self._rownumber += len(rows)
            async for state in generator:
                if state in ("DATA_ROW", "DATA_BATCH"):
                    rows.extend(self.cached_rows)
                    self._rownumber += len(self.cached_rows)
                    self.cached_rows.clear()
                elif state == "COMMAND_COMPLETE":
                    self.has_rows = len(rows) > 0
                    continue
                elif state in ("ROW_DESCRIPTION", "DESCRIPTION", "DBOS_COLUMN_DESCRIPTION"):
                    self.has_rows = (
                        len(self.cached_rows) > 0 or
                        (self.ps is not None and len(self.ps.get('row_desc', [])) > 0)
                    )
                    self.generator = generator
                    return [list(r) for r in rows]
                elif state == "READY_FOR_QUERY":
                    self.generator = None
                    return [list(r) for r in rows]
                elif state == "ERROR":
                    err = self._c.error if self._c is not None else None
                    if self._c is not None:
                        await self._c.drain_protocol_generator(generator)
                    self.generator = None
                    if err is not None:
                        raise ProgrammingError(err)
                    return [list(r) for r in rows]
            self.generator = None
            return [list(r) for r in rows]
        except TypeError:
            raise ProgrammingError("attempting to use unexecuted cursor")

    async def cancel(self, exec_gen: int | None = None) -> None:
        if self._c is not None:
            await self._c.cancel(exec_gen)

    async def close(self) -> None:
        generator = getattr(self, 'generator', None)
        conn = self._c
        if generator is not None:
            if conn is not None:
                await conn.drain_protocol_generator(generator)
            self.generator = None
        self.cached_rows.clear()
        if conn is not None and getattr(conn, '_active_cursor', None) is self:
            conn._active_cursor = None
        self._c = None

    def __aiter__(self) -> Cursor:
        return self

    def setinputsizes(self, sizes: Any) -> None:
        pass

    def setoutputsize(self, size: Any, column: Any = None) -> None:
        pass

    async def __anext__(self) -> Any:
        self._ensure_open()
        if getattr(self, '_timeout', None) is not None and self._timeout and self._timeout > 0:
            try:
                row = await asyncio.wait_for(self._anext_internal(), timeout=self._timeout)
            except asyncio.TimeoutError:
                if self._c is not None:
                    await self._c.cancel(exec_gen=getattr(self, '_exec_gen', None))
                raise OperationalError("Command fetch timeout")
        else:
            row = await self._anext_internal()
        self._rownumber += 1
        return list(row)

    async def _anext_internal(self) -> Any:
        try:
            return self.cached_rows.popleft()
        except IndexError:
            generator = getattr(self, 'generator', None)
            if generator is not None:
                while True:
                    try:
                        state = await generator.__anext__()
                    except StopAsyncIteration:
                        break
                    if state in ("DATA_ROW", "DATA_BATCH"):
                        if len(self.cached_rows) > 0:
                            return self.cached_rows.popleft()
                    elif state == "COMMAND_COMPLETE":
                        if not self.cached_rows:
                            raise StopAsyncIteration()
                        continue
                    elif state == "READY_FOR_QUERY":
                        self.generator = None
                        raise StopAsyncIteration()
                    elif state == "ERROR":
                        err = self._c.error if self._c is not None else None
                        if self._c is not None:
                            await self._c.drain_protocol_generator(generator)
                        self.generator = None
                        if err is not None:
                            raise ProgrammingError(err)
                        raise StopAsyncIteration()
                self.generator = None
                raise StopAsyncIteration()

            if self.ps is None:
                raise ProgrammingError("A query hasn't been issued.")
            elif len(self.ps['row_desc']) == 0:
                raise ProgrammingError("no result set")
            else:
                raise StopAsyncIteration()

    async def clear(self) -> None:
        generator = getattr(self, 'generator', None)
        if generator is not None:
            if self._c is not None:
                await self._c.drain_protocol_generator(generator)
            else:
                async for _ in generator:
                    pass
            self.generator = None

        self.ps = None
        self.row_count = -1
        self.has_rows = False
        self.cached_rows.clear()
        self._rownumber = 0

    async def nextset(self) -> bool | None:
        self.cached_rows.clear()

        generator = getattr(self, 'generator', None)
        if generator is None:
            return None

        while True:
            try:
                state = await generator.__anext__()
            except StopAsyncIteration:
                self.generator = None
                return None

            if state in ("ROW_DESCRIPTION", "DESCRIPTION", "DBOS_COLUMN_DESCRIPTION"):
                self.has_rows = (
                    len(self.cached_rows) > 0 or
                    (self.ps is not None and len(self.ps.get('row_desc', [])) > 0)
                )
                return True
            elif state == "COMMAND_COMPLETE":
                continue
            elif state == "READY_FOR_QUERY":
                self.generator = None
                return None
            elif state == "ERROR":
                err = self._c.error if self._c is not None else None
                if self._c is not None:
                    await self._c.drain_protocol_generator(generator)
                self.generator = None
                if err is not None:
                    raise ProgrammingError(err)
                return None
            if state in ("DATA_ROW", "DATA_BATCH"):
                self.has_rows = len(self.cached_rows) > 0
                return True


__all__ = ["Cursor"]
