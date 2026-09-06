"""Monaco language-service endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_language_service
from app.services.language_service import LanguageService

router = APIRouter(tags=["language"])


@router.post("/api/v1/language/completion")
async def completion(payload: dict, service: LanguageService = Depends(get_language_service)) -> dict:
    return await service.completion(str(payload.get("sql", "")), int(payload.get("offset", 0)), payload.get("database"), payload.get("schema"))


@router.post("/api/v1/language/diagnostics")
async def diagnostics(payload: dict, service: LanguageService = Depends(get_language_service)) -> dict:
    return await service.diagnostics(str(payload.get("sql", "")), payload.get("database"), payload.get("schema"))


@router.post("/api/v1/language/format")
async def format_sql(payload: dict) -> dict[str, str]:
    sql = str(payload.get("sql", ""))
    lines = [line.rstrip() for line in sql.strip().splitlines()]
    return {"sql": "\n".join(lines)}
