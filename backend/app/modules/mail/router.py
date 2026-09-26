"""Public HTTP composition for the mail feature.

Route modules only translate HTTP input/output. Domain work remains in the
mail services, repositories and the bounded Agent implementation.
"""

from fastapi import APIRouter

from backend.app.modules.mail.routes import (
    accounts,
    assistant,
    imports,
    messages,
    perception,
    settings,
    work_items,
)
from backend.app.modules.mail.records import router as records_router
from backend.app.observability.mail import router as observability_router


router = APIRouter(prefix="/api")

for feature_router in (
    accounts.router,
    messages.router,
    imports.router,
    assistant.router,
    work_items.router,
    perception.router,
    settings.router,
    observability_router,
    records_router,
):
    router.include_router(feature_router)
