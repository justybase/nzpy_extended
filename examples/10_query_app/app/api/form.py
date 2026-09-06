"""Lazy form parsing for legacy multipart compatibility endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request


async def read_form(request: Request) -> Any:
    """Parse a form only when a legacy endpoint is actually used.

    FastAPI validates ``Form``/``File`` dependencies while importing routes.
    Keeping parsing here lets the JSON/WebSocket workspace boot without the
    optional multipart package; the compatibility endpoints still explain
    the missing dependency when called.
    """
    try:
        return await request.form()
    except (AssertionError, RuntimeError) as exc:
        raise HTTPException(
            503,
            "Multipart form support is unavailable. Install python-multipart.",
        ) from exc
