"""Async query lifecycle with WebSocket-friendly events and cancel support."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

import nzpy_extended as nzpy

from app.core.jsonconv import jsonable_rows
from app.services.result_session_service import ResultSessionManager
from app.services.sql_document import statement_for_cursor, split_statements

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


def build_columns(cur: Any) -> list[dict[str, Any]]:
    get_schema = getattr(cur, "get_schema_table", None)
    schema = get_schema() if callable(get_schema) else None
    if schema:
        out: list[dict[str, Any]] = []
        for col in schema:
            item = dict(col)
            data_type = item.get("DataType")
            if isinstance(data_type, type):
                item["DataType"] = data_type.__name__
            out.append(item)
        return out
    desc = getattr(cur, "description", None)
    if desc:
        return [{"ColumnName": d[0], "DataType": "unknown", "ColumnOrdinal": i + 1} for i, d in enumerate(desc)]
    return []


class QueryService:
    def __init__(self, pool_getter: Any, result_sessions: ResultSessionManager | None = None, result_limit: int = 1_000_000, chunk_size: int = 1_000) -> None:
        self._pool_getter = pool_getter
        self._result_sessions = result_sessions or ResultSessionManager("/tmp/nzpy_extended-test-sessions", 60)
        self._result_limit = max(1, result_limit)
        self._chunk_size = max(1, chunk_size)
        self._active: dict[str, dict[str, Any]] = {}

    @property
    def active(self) -> dict[str, dict[str, Any]]:
        return self._active

    async def _pool(self, database: str | None = None) -> Any:
        try:
            value = self._pool_getter(database)
        except TypeError:
            value = self._pool_getter()
        if hasattr(value, "__await__"):
            return await value
        return value

    async def start(self, query_id: str, sql: str, *, mode: str, cursor_offset: int | None, selection: dict[str, int] | None, database: str | None, timeout: float | None, emit: EventSink) -> asyncio.Task[None]:
        task = asyncio.create_task(self._run(query_id, sql, mode, cursor_offset, selection, database, timeout, emit))
        self._active[query_id] = {"task": task, "cancelled": False, "cursor": None, "conn": None}
        task.add_done_callback(lambda _: self._active.pop(query_id, None))
        return task

    async def _run(self, query_id: str, sql: str, mode: str, cursor_offset: int | None, selection: dict[str, int] | None, database: str | None, timeout: float | None, emit: EventSink) -> None:
        started = time.monotonic()
        statements = split_statements(sql)
        if mode == "cursor" and cursor_offset is not None:
            selected = statement_for_cursor(sql, cursor_offset)
            statements = [selected] if selected else []
        elif mode == "selection" and selection:
            start = max(0, int(selection.get("start", 0)))
            end = max(start, int(selection.get("end", len(sql))))
            statements = split_statements(sql[start:end])
        sequence = 0

        async def send(event: dict[str, Any]) -> None:
            nonlocal sequence
            sequence += 1
            event["sequence"] = sequence
            await emit(event)

        if not statements:
            await send({"type": "error", "queryId": query_id, "message": "SQL query is empty"})
            await send({"type": "batch-complete", "queryId": query_id, "status": "error", "completedStatements": 0, "statementCount": 0})
            return
        await send({"type": "started", "queryId": query_id, "statementCount": len(statements), "startedAt": time.time()})
        completed = 0
        terminal_status = "error"
        current_session_id: str | None = None
        current_result_set_id: str | None = None
        current_statement_index: int | None = None
        try:
            pool = await self._pool(database)
            async with pool.connection() as conn:
                self._active[query_id]["conn"] = conn
                cur = conn.cursor()
                self._active[query_id]["cursor"] = cur
                try:
                    for statement in statements:
                        if self._active.get(query_id, {}).get("cancelled"):
                            raise asyncio.CancelledError
                        current_statement_index = statement.index
                        await send({"type": "statement-started", "queryId": query_id, "statementIndex": statement.index, "statementCount": len(statements), "statementSql": statement.sql})
                        await cur.execute(statement.sql, timeout=timeout)
                        columns = build_columns(cur)
                        result_set_id = uuid4().hex
                        current_result_set_id = result_set_id
                        manifest = self._result_sessions.create(query_id, result_set_id, statement.index, len(statements), columns)
                        current_session_id = manifest["session_id"]
                        await send({"type": "columns", "queryId": query_id, "statementIndex": statement.index, "columns": columns})
                        await send({"type": "session-created", "queryId": query_id, "statementIndex": statement.index, "resultSetId": result_set_id, "sessionId": manifest["session_id"]})
                        total = 0
                        truncated = False
                        while True:
                            remaining = self._result_limit - total
                            batch = await cur.fetchmany(min(self._chunk_size, remaining + 1))
                            if not batch:
                                break
                            rows = jsonable_rows([list(row) for row in batch[:remaining]])
                            if rows:
                                total = self._result_sessions.append(manifest["session_id"], rows)
                                await send({"type": "progress", "queryId": query_id, "statementIndex": statement.index, "sessionId": manifest["session_id"], "totalRows": total})
                            if len(batch) > remaining:
                                truncated = True
                                break
                            if self._active.get(query_id, {}).get("cancelled"):
                                raise asyncio.CancelledError
                        rowcount = getattr(cur, "rowcount", -1)
                        final = self._result_sessions.complete(manifest["session_id"], rows_affected=rowcount if rowcount >= 0 else None, truncated=truncated, message=f"Showing first {self._result_limit:,} rows" if truncated else None)
                        await send({"type": "complete", "queryId": query_id, "statementIndex": statement.index, "sessionId": manifest["session_id"], "resultSetId": result_set_id, "totalRows": final["total_rows"], "limitReached": truncated, "rowsAffected": final.get("rows_affected"), "elapsedMs": (time.monotonic() - started) * 1000})
                        current_session_id = None
                        current_result_set_id = None
                        current_statement_index = None
                        completed += 1
                    terminal_status = "complete"
                finally:
                    await cur.close()
        except asyncio.CancelledError:
            terminal_status = "cancelled"
            if current_session_id:
                self._result_sessions.complete(current_session_id, status="cancelled", message="Query cancelled")
            await send({"type": "cancelled", "queryId": query_id, "statementIndex": current_statement_index, "sessionId": current_session_id, "resultSetId": current_result_set_id, "completedStatements": completed})
        except (nzpy.Error, Exception) as exc:
            if self._active.get(query_id, {}).get("cancelled"):
                terminal_status = "cancelled"
                if current_session_id:
                    self._result_sessions.complete(current_session_id, status="cancelled", message="Query cancelled")
                await send({"type": "cancelled", "queryId": query_id, "statementIndex": current_statement_index, "sessionId": current_session_id, "resultSetId": current_result_set_id, "completedStatements": completed})
            else:
                if current_session_id:
                    self._result_sessions.complete(current_session_id, status="error", message=str(exc))
                await send({"type": "error", "queryId": query_id, "statementIndex": current_statement_index, "sessionId": current_session_id, "resultSetId": current_result_set_id, "message": f"{type(exc).__name__}: {exc}", "completedStatements": completed})
        finally:
            await send({"type": "batch-complete", "queryId": query_id, "status": terminal_status, "completedStatements": completed, "statementCount": len(statements)})

    async def cancel(self, query_id: str) -> bool:
        entry = self._active.get(query_id)
        if entry is None:
            return False
        entry["cancelled"] = True
        conn = entry.get("conn")
        if conn is not None:
            try:
                await conn.cancel()
            except Exception:
                pass
        return True

    def request_cancel(self, query_id: str) -> bool:
        """Legacy synchronous flag API retained for existing unit callers."""
        entry = self._active.get(query_id)
        if entry is None:
            return False
        entry["cancelled"] = True
        return True

    async def execute(self, query_id: str, sql: str, timeout: float | None) -> tuple[list[dict[str, Any]], list[list[Any]], int, float]:
        """Compatibility helper retained for the old blocking endpoint."""
        pool = await self._pool(None)
        started = time.monotonic()
        async with pool.connection() as conn:
            cur = conn.cursor()
            try:
                await cur.execute(sql, timeout=timeout)
                rows = [list(row) for row in await cur.fetchall()]
                columns = build_columns(cur)
                return columns, jsonable_rows(rows), getattr(cur, "rowcount", len(rows)), (time.monotonic() - started) * 1000
            finally:
                await cur.close()
