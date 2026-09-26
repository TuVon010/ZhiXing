"""Compose public API routers without importing UI or worker internals."""

from fastapi import APIRouter

from backend.app.api.routes import automation, filtering, pricing, system
from backend.app.modules.mail.router import router as mail_router


api_router = APIRouter()

# Keep the generic ``/{collection}`` routes last so concrete endpoints win.
for router in (
    system.router,
    filtering.router,
    pricing.router,
    mail_router,
    automation.router,
):
    api_router.include_router(router)
