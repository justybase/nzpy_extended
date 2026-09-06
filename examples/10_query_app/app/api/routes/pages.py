"""Landing page — serves the static shell."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from app.api.deps import get_settings
from app.core.config import Settings

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def index(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    html_path = settings.static_dir / "dist" / "index.html"
    if not html_path.exists():
        html_path = settings.static_dir / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    raise HTTPException(404, "index.html not found")
