"""Route modules, aggregated into one API router."""

from fastapi import APIRouter

from app.api.routes import (auth, cache, drill, exports, ledger, meta, pages,
                             people, reports, session, status, temporal)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(pages.router)
api_router.include_router(meta.router)
api_router.include_router(reports.router)
api_router.include_router(drill.router)
api_router.include_router(exports.router)
api_router.include_router(ledger.router)
api_router.include_router(people.router)
api_router.include_router(session.router)
api_router.include_router(cache.router)
api_router.include_router(status.router)
api_router.include_router(temporal.router)
