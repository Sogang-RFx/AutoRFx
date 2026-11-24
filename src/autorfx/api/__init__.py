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
from autorfx.api import auth_routes, routes
from autorfx.api.security import AuthGate, ResponseHeaders, error_response, protect
from autorfx.api.state import ApiError, AppState
from autorfx.auth import AuthError, PolicyError
from autorfx.config import Settings, get_settings
from autorfx.rules import Ruleset

__all__ = ["ApiError", "AppState", "create_app"]

log = logging.getLogger(__name__)


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

    app = FastAPI(
        title="AutoRFx API",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # The last one added runs first: headers, CORS, host, gate, route. The gate is inside CORS so that
    # the web app can read its refusals; a request for an unknown host does not get as far as the gate.
    app.add_middleware(AuthGate, origins=settings.cors_origin_list)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_host_list))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origin_list),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
        expose_headers=["Content-Disposition"],
    )

    app.add_middleware(ResponseHeaders)

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(AuthError)
    async def auth_error(_: Request, exc: AuthError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(PolicyError)
    async def policy_error(_: Request, exc: PolicyError) -> JSONResponse:
        return error_response(403, "forbidden", str(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        return error_response(422, "invalid_request", f"요청 형식이 맞지 않습니다: {where or '본문'}")

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        message = "요청한 주소가 없습니다." if exc.status_code == 404 else "요청을 처리할 수 없습니다."
        return error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        # The detail goes to the log, never to the client.
        log.exception("unhandled error", exc_info=exc)
        response = error_response(500, "internal", "서버 오류가 발생했습니다. 잠시 뒤에 다시 시도해 주세요.")
        protect(response.headers)  # this handler runs outside every middleware
        return response

    for router in (
        routes.open_router,
        auth_routes.open_router,
        auth_routes.router,
        auth_routes.admin_router,
        routes.router,
    ):
        app.include_router(router)
    return app
