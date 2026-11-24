"""The gate: who may reach a route at all (docs/auth-rbac.md §3).

:class:`AuthGate` sits inside CORS and in front of every route, so a new route
needs a session without doing anything for it, and a refused request is answered
before its body is read. The dependencies below say the same again at the route
and hand it the viewer; a route that lost the gate would still refuse.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from autorfx.api.state import ApiError, AppState
from autorfx.auth import AuthError, PolicyError, Viewer, can_manage, can_upload, scope_of
from autorfx.auth import Scope as AccessScope
from autorfx.models import Group

COOKIE = "autorfx_session"
LOGIN = ("POST", "/auth/login")
OPEN = frozenset({("GET", "/health"), LOGIN})
LOGOUT = ("POST", "/auth/logout")
OWN_ACCOUNT = frozenset({("GET", "/auth/me"), ("POST", "/auth/password"), LOGOUT})
"""What an account that must change its password may still do."""
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_LOGIN_BODY = 16 * 1024
"""The only body that is read without a session."""

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

UNAUTHENTICATED = "로그인이 필요합니다."


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def protect(headers: MutableHeaders) -> None:
    """Headers of every response. Any of them may depend on the cookie, so none is stored."""
    for name, value in HEADERS.items():
        headers.setdefault(name, value)
    headers["Cache-Control"] = "no-store"
    headers.add_vary_header("Cookie")


class ResponseHeaders:
    """Adds :func:`protect` to every response, refusals and preflights included."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def protected(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                protect(MutableHeaders(scope=message))
            await send(message)

        await self.app(scope, receive, protected)


class AuthGate:
    """No session, no route: everything but :data:`OPEN` needs a valid session cookie."""

    def __init__(self, app: ASGIApp, *, origins: tuple[str, ...]) -> None:
        self.app = app
        self.origins = frozenset(origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":  # there are no websocket routes; nothing else is let through
            await send({"type": "websocket.close", "code": 1008})
            return
        try:
            scope, receive = await self._admit(scope, receive)
        except (ApiError, AuthError) as exc:
            await error_response(exc.status, exc.code, exc.message)(scope, receive, send)
            return
        except PolicyError as exc:
            await error_response(403, "forbidden", str(exc))(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _admit(self, scope: Scope, receive: Receive) -> tuple[Scope, Receive]:
        """The scope with the viewer in it, or the refusal as an exception."""
        route = (scope["method"], scope["path"])
        headers = Headers(scope=scope)
        if scope["method"] not in SAFE_METHODS and not self._is_own_origin(headers):
            raise ApiError(403, "bad_origin", "허용되지 않은 화면 주소에서 온 요청입니다.")
        if route == LOGIN:
            return scope, await _limited(headers, receive)
        if route in OPEN:
            return scope, receive
        token = session_token(Request(scope))
        if not token:
            raise ApiError(401, "unauthenticated", UNAUTHENTICATED)
        state = app_state(scope)
        viewer = await run_in_threadpool(state.auth.viewer_for, token)
        if viewer is None:
            raise ApiError(401, "unauthenticated", UNAUTHENTICATED)
        if viewer.must_change_password and route not in OWN_ACCOUNT:
            raise ApiError(403, "password_change_required", "비밀번호를 먼저 바꿔 주세요.")
        if route != LOGOUT:
            scope_of(viewer, state.ruleset)  # an account without a valid scope reaches no route
        return {**scope, "state": {**scope.get("state", {}), "viewer": viewer}}, receive

    def _is_own_origin(self, headers: Headers) -> bool:
        given = headers.getlist("origin")
        return len(given) == 1 and given[0] in self.origins


async def _limited(headers: Headers, receive: Receive) -> Receive:
    """``receive`` with the whole body already read, or 413 once it is longer than a login can be."""
    too_large = ApiError(413, "too_large", "요청이 너무 큽니다.")
    declared = headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_LOGIN_BODY:
        raise too_large
    messages: list[Message] = []
    size = 0
    while True:
        message = await receive()
        messages.append(message)
        size += len(message.get("body", b""))
        if size > MAX_LOGIN_BODY:
            raise too_large
        if message["type"] != "http.request" or not message.get("more_body", False):
            break
    pending = iter(messages)

    async def replay() -> Message:
        message = next(pending, None)
        return message if message is not None else await receive()

    return replay


def app_state(scope: Scope) -> AppState:
    state = getattr(scope["app"].state, "autorfx", None)
    if state is None:
        raise ApiError(503, "not_ready", "서버가 아직 준비되지 않았습니다.")
    return state


def session_token(request: Request) -> str:
    return request.cookies.get(COOKIE, "")


# ── dependencies ────────────────────────────────────────────────────────────
def state_of(request: Request) -> AppState:
    return app_state(request.scope)


State = Annotated[AppState, Depends(state_of)]


async def current_viewer(request: Request) -> Viewer:
    """The account the gate found; without the gate nobody is logged in."""
    viewer = getattr(request.state, "viewer", None)
    if not isinstance(viewer, Viewer):
        raise ApiError(401, "unauthenticated", UNAUTHENTICATED)
    return viewer


CurrentViewer = Annotated[Viewer, Depends(current_viewer)]


def admin_viewer(request: Request, viewer: CurrentViewer, state: State) -> Viewer:
    if not can_manage(viewer):
        what = f"{request.method} {request.scope['route'].path}"
        state.audit.record("admin", actor=viewer.username, target=what, outcome="denied")
        raise ApiError(403, "forbidden", "관리자만 할 수 있습니다.")
    return viewer


AdminViewer = Annotated[Viewer, Depends(admin_viewer)]


def uploading_viewer(viewer: CurrentViewer, state: State) -> Viewer:
    if not can_upload(viewer, state.ruleset):
        state.audit.record("upload", actor=viewer.username, outcome="denied")
        raise ApiError(403, "forbidden", "RFQ를 올릴 수 있는 계정이 아닙니다.")
    return viewer


UploadingViewer = Annotated[Viewer, Depends(uploading_viewer)]


async def current_scope(viewer: CurrentViewer, state: State) -> AccessScope:
    return scope_of(viewer, state.ruleset)


CurrentScope = Annotated[AccessScope, Depends(current_scope)]


async def current_group(scope: CurrentScope) -> Group:
    return scope.group


CurrentGroup = Annotated[Group, Depends(current_group)]
