"""FastAPI application assembly.

Business routes, persistence and worker concerns live in dedicated packages.
This module intentionally contains no endpoint implementation.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.agent.evolution import seed
from backend.app.api.router import api_router
from backend.app.api.web import mount_frontend
from backend.app.core.security import install_http_guards
from backend.app.persistence.store import store


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize built-in versioned assets without starting worker threads."""
    seed(store)
    yield


def create_app() -> FastAPI:
    """Build the HTTP application from independently testable routers."""
    application = FastAPI(
        title="知行 ZhiXing Personal Agent",
        version="1.0.0",
        lifespan=lifespan,
    )
    install_http_guards(application)
    application.include_router(api_router)
    mount_frontend(application)
    return application


app = create_app()
