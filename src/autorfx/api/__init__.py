"""The HTTP API (SPEC §19). ``create_app`` builds the application; ``autorfx serve`` runs it."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from autorfx.analysis import AnalysisProvider
from autorfx.api.routes import router
from autorfx.api.state import ApiError, AppState
from autorfx.config import Settings, get_settings
from autorfx.rules import Ruleset

__all__ = ["ApiError", "AppState", "create_app"]

log = logging.getLogger(__name__)
HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(
    settings: Settings | None = None,
    *,
    ruleset: Ruleset | None = None,
    provider: AnalysisProvider | None = None,
) -> FastAPI:
    """The application. ``ruleset`` and ``provider`` are injected by tests."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = AppState(settings, ruleset=ruleset, provider=provider)
        app.state.autorfx = state
        log.info("engines: %s", state.engines)
        try:
            yield
        finally:
            state.close()

    app = FastAPI(title="AutoRFx API", version="0.2.0", lifespan=lifespan)
    # Added before CORS, so it runs after it: a request for an unknown host never reaches a route.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_host_list))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origin_list),
        allow_methods=["GET", "POST", "PUT"],  # PUT: department settings (SPEC US10)
        allow_headers=["Content-Type"],
        expose_headers=["Content-Disposition"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        for name, value in HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        return _error(422, "invalid_request", f"요청 형식이 맞지 않습니다: {where or '본문'}")

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        message = "요청한 주소가 없습니다." if exc.status_code == 404 else "요청을 처리할 수 없습니다."
        return _error(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        # The detail goes to the log, never to the client.
        log.exception("unhandled error", exc_info=exc)
        return _error(500, "internal", "서버 오류가 발생했습니다. 잠시 뒤에 다시 시도해 주세요.")

    app.include_router(router)
    return app
