"""Result-session manifest, page, export and cleanup routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.api.deps import get_result_sessions
from app.services.result_session_service import ResultSessionManager

router = APIRouter(tags=["results"])


@router.get("/api/v1/results/{session_id}")
async def result_manifest(session_id: str, sessions: ResultSessionManager = Depends(get_result_sessions)) -> dict:
    try:
        return sessions.manifest(session_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/v1/results/{session_id}/page")
async def result_page(session_id: str, payload: dict, sessions: ResultSessionManager = Depends(get_result_sessions)) -> dict:
    try:
        return sessions.page(
            session_id,
            offset=payload.get("offset", 0),
            limit=payload.get("limit"),
            global_filter=str(payload.get("globalFilter", "")),
            column_filters=payload.get("columnFilters") or [],
            sorting=payload.get("sorting") or [],
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/v1/results/{session_id}/export")
async def export_session(session_id: str, payload: dict, sessions: ResultSessionManager = Depends(get_result_sessions)) -> Response:
    fmt = str(payload.get("format", "csv")).lower()
    if fmt not in {"csv", "json"}:
        raise HTTPException(400, "format must be csv or json")
    try:
        body, media_type, filename = sessions.export(
            session_id,
            fmt,
            global_filter=str(payload.get("globalFilter", "")),
            column_filters=payload.get("columnFilters") or [],
            sorting=payload.get("sorting") or [],
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(body, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.delete("/api/v1/results/{session_id}")
async def delete_session(session_id: str, sessions: ResultSessionManager = Depends(get_result_sessions)) -> dict[str, str]:
    sessions.delete(session_id)
    return {"status": "deleted", "sessionId": session_id}
