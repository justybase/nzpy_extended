"""HTTP route modules."""

from fastapi import APIRouter

from app.api.routes.cockpit import router as cockpit_router
from app.api.routes.exports import router as exports_router
from app.api.routes.meta import router as meta_router
from app.api.routes.pages import router as pages_router
from app.api.routes.status import router as status_router

api_router = APIRouter()
api_router.include_router(pages_router)
api_router.include_router(meta_router)
api_router.include_router(cockpit_router)
api_router.include_router(exports_router)
api_router.include_router(status_router)
