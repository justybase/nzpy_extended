"""Aggregate router for the query app."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import export, import_, language, pages, query, results, schema, version

api_router = APIRouter()
api_router.include_router(pages.router)
api_router.include_router(query.router)
api_router.include_router(results.router)
api_router.include_router(language.router)
api_router.include_router(schema.router)
api_router.include_router(export.router)
api_router.include_router(import_.router)
api_router.include_router(version.router)
