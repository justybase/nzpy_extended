"""Frontend entry point."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from app.api.deps import get_settings
from app.core.config import Settings

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    html = settings.static_dir / "index.html"
    if html.exists():
        return HTMLResponse(html.read_text(encoding="utf-8"))
    raise HTTPException(404, "index.html not found")
