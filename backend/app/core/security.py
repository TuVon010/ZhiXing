"""Local-only HTTP boundary for the single-user desktop application.

The browser session cookie and request marker protect state-changing endpoints
from unrelated local pages.  Mailbox credentials are handled separately by the
DPAPI credential store.
"""

import json
import os
import secrets

from fastapi import FastAPI, Request, Response


SESSION_TOKEN = secrets.token_urlsafe(32)


def install_http_guards(app: FastAPI) -> None:
    """Install host/origin checks and translate expected domain errors."""

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0]
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return Response("Forbidden host", 403)

        public_api = {"/api/session", "/api/health"}
        if request.url.path.startswith("/api/") and request.url.path not in public_api:
            cookie = request.cookies.get("zhixing_session", "")
            if not secrets.compare_digest(cookie, SESSION_TOKEN):
                return Response("请刷新本地页面建立会话", 401)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                allowed = {
                    "http://127.0.0.1:8000",
                    "http://localhost:8000",
                    "http://127.0.0.1:5173",
                    "http://localhost:5173",
                }
                if origin := os.environ.get("ZHIXING_E2E_ORIGIN"):
                    allowed.add(origin)
                request_origin = request.headers.get("origin")
                if request_origin and request_origin not in allowed:
                    return Response("Forbidden origin", 403)
                if request.headers.get("x-zhixing-local") != "1":
                    return Response("Missing local request marker", 403)
        try:
            return await call_next(request)
        except (ValueError, KeyError) as exc:
            status = 404 if isinstance(exc, KeyError) else 400
            detail = "记录不存在" if status == 404 else str(exc)
            return Response(
                json.dumps({"detail": detail}, ensure_ascii=False),
                status,
                media_type="application/json",
            )

    @app.exception_handler(ValueError)
    async def value_error(_request, exc):
        return Response(
            json.dumps({"detail": str(exc)}, ensure_ascii=False),
            400,
            media_type="application/json",
        )

    @app.exception_handler(KeyError)
    async def missing(_request, _exc):
        return Response(
            json.dumps({"detail": "记录不存在"}, ensure_ascii=False),
            404,
            media_type="application/json",
        )
