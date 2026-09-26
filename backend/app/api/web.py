"""Optional production adapter that serves the independently built React app."""

import mimetypes

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.core.config import ROOT


def mount_frontend(app: FastAPI) -> None:
    """Serve ``frontend/dist`` for the one-command local distribution.

    Development remains frontend/backend separated: Vite talks to the HTTP API.
    This adapter only packages the compiled UI with the local FastAPI process.
    """

    dist = ROOT / "frontend" / "dist"
    if not dist.exists():
        return
    mimetypes.init()
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/zhixing-mark.svg", include_in_schema=False)
    def brand_icon():
        return FileResponse(dist / "zhixing-mark.svg", media_type="image/svg+xml")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(dist / "index.html")
