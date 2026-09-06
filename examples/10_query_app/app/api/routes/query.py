"""Query preview, compatibility execution and workspace WebSocket."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import nzpy_extended as nzpy
from fastapi import APIRouter, Depends, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.api.deps import get_query_service, get_settings, get_sql_safety
from app.core.config import Settings
from app.schemas.models import CancelResponse
from app.services.query_service import QueryService
from app.services.sql_safety import SqlSafetyService

router = APIRouter(tags=["query"])


@router.post("/api/v1/query/preview")
async def preview_query(payload: dict[str, Any], safety: SqlSafetyService = Depends(get_sql_safety)) -> dict[str, Any]:
    sql = str(payload.get("sql", ""))
    if not sql.strip():
        raise HTTPException(400, "SQL query is empty")
    return safety.preview(sql, payload.get("database"))


@router.post("/api/v1/query/start")
async def start_query(payload: dict[str, Any]) -> dict[str, Any]:
    if not str(payload.get("sql", "")).strip():
        raise HTTPException(400, "SQL query is empty")
    return {"queryId": payload.get("queryId") or os.urandom(12).hex(), "transport": "websocket"}


@router.websocket("/api/v1/workspace/ws")
async def workspace_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    service: QueryService = websocket.app.state.query_service
    safety: SqlSafetyService = websocket.app.state.sql_safety
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    sender_done = asyncio.Event()
    owned_query_ids: set[str] = set()

    async def sender() -> None:
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except Exception:
            pass
        finally:
            sender_done.set()

    sender_task = asyncio.create_task(sender())
    try:
        while True:
            message = await websocket.receive_json()
            kind = message.get("type")
            if kind == "query.start":
                sql = str(message.get("sql", ""))
                if not sql.strip():
                    await queue.put({"type": "error", "queryId": message.get("queryId"), "message": "SQL query is empty"})
                    continue
                query_id = str(message.get("queryId") or os.urandom(12).hex())
                owned_query_ids.add(query_id)
                database = message.get("database")
                if not safety.validate(message.get("previewToken"), sql, database, bool(message.get("writeConfirmed"))):
                    await queue.put({"type": "error", "queryId": query_id, "message": "Write statement requires a valid preview confirmation."})
                    continue
                await service.start(
                    query_id,
                    sql,
                    mode=str(message.get("mode", "cursor")),
                    cursor_offset=message.get("cursorOffset"),
                    selection=message.get("selection"),
                    database=database,
                    timeout=float(message.get("timeoutSeconds") or websocket.app.state.settings.default_query_timeout),
                    emit=queue.put,
                )
            elif kind == "query.cancel":
                query_id = str(message.get("queryId", ""))
                await queue.put({"type": "cancel-requested", "queryId": query_id, "accepted": await service.cancel(query_id)})
            elif kind == "workspace.ping":
                await queue.put({"type": "workspace.pong"})
    except WebSocketDisconnect:
        return
    finally:
        for query_id in owned_query_ids:
            await service.cancel(query_id)
        sender_task.cancel()
        await sender_done.wait()


@router.post("/api/query")
async def run_query(
    sql: str = Form(...),
    timeout: float | None = Form(None),
    query_id: str | None = Form(None),
    preview_token: str | None = Form(None),
    write_confirmed: bool = Form(False),
    settings: Settings = Depends(get_settings),
    service: QueryService = Depends(get_query_service),
    safety: SqlSafetyService = Depends(get_sql_safety),
) -> JSONResponse:
    if not sql.strip():
        raise HTTPException(400, "SQL query is empty")
    if not safety.validate(preview_token, sql, None, write_confirmed):
        raise HTTPException(409, "Write statement requires a valid preview confirmation.")
    qid = query_id or os.urandom(8).hex()
    effective = timeout if timeout is not None and timeout > 0 else settings.default_query_timeout
    try:
        columns, rows, row_count, elapsed_ms = await service.execute(qid, sql.strip(), effective)
    except nzpy.OperationalError as exc:
        raise HTTPException(408, f"Query timeout: {exc}") from exc
    except nzpy.Error as exc:
        raise HTTPException(500, str(exc)) from exc
    payload: dict[str, Any] = {"query_id": qid, "columns": columns, "row_count": row_count, "truncated": len(rows) > settings.result_limit, "elapsed_ms": elapsed_ms, "rows": rows[: settings.result_limit]}
    if payload["truncated"]:
        payload["message"] = f"Showing first {settings.result_limit} rows"
    return JSONResponse(content=payload)


@router.post("/api/cancel", response_model=CancelResponse)
async def cancel_query(query_id: str = Form(...), service: QueryService = Depends(get_query_service)) -> CancelResponse:
    accepted = await service.cancel(query_id)
    return CancelResponse(status="cancelling" if accepted else "no_active_query", query_id=query_id)
