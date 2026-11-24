"""Login, the own account and account management (docs/auth-rbac.md §3).

Passwords are checked and hashed on a small pool of their own
(``AppState.run_password_work``): a flood of logins waits there, and neither
uses up the memory nor the threads the other routes need.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from autorfx.api.security import (
    COOKIE,
    AdminViewer,
    CurrentViewer,
    State,
    admin_viewer,
    current_viewer,
    session_token,
)
from autorfx.api.state import ApiError, AppState
from autorfx.auth import (
    AuthError,
    LockedError,
    PolicyError,
    Role,
    UserRecord,
    Viewer,
    can_manage,
    can_upload,
    scope_of,
)
from autorfx.auth.passwords import MAX_LENGTH, MIN_LENGTH
from autorfx.auth.store import UNSET
from autorfx.models import Model

open_router = APIRouter()
router = APIRouter(dependencies=[Depends(current_viewer)])
admin_router = APIRouter(prefix="/admin", dependencies=[Depends(admin_viewer)])

WRONG_CURRENT = "현재 비밀번호가 맞지 않습니다."

NewPassword = Annotated[str, Field(min_length=MIN_LENGTH, max_length=MAX_LENGTH)]
DisplayName = Annotated[str, Field(min_length=1, max_length=60)]
Department = Annotated[str, Field(min_length=1, max_length=64)]


class LoginBody(Model):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=MAX_LENGTH)


class PasswordBody(Model):
    current_password: str = Field(min_length=1, max_length=MAX_LENGTH)
    new_password: NewPassword


class CreateBody(Model):
    username: str = Field(min_length=1, max_length=64)
    display_name: DisplayName
    password: NewPassword
    role: Role
    department: Department | None


class PatchBody(Model):
    """Only what is sent changes; the defaults stand for "not sent" and are never read."""

    display_name: DisplayName = ""
    role: Role = "member"
    department: Department | None = None
    disabled: bool = False


class ResetBody(Model):
    password: NewPassword


# ── payloads ────────────────────────────────────────────────────────────────
def viewer_payload(viewer: Viewer, state: AppState) -> dict:
    scope = scope_of(viewer, state.ruleset)
    rule = state.ruleset.department(viewer.department) if viewer.department else None
    data = {
        "user_id": viewer.user_id,
        "username": viewer.username,
        "display_name": viewer.display_name,
        "role": viewer.role,
        "department": viewer.department,
        "department_label": rule.label if rule else None,
        "group": scope.group,
        "full_scope": scope.is_full,
        "can_upload": can_upload(viewer, state.ruleset),
        "can_manage": can_manage(viewer),
        "must_change_password": viewer.must_change_password,
    }
    return {"data": {"viewer": data}, "provenance": {}}


def user_fields(user: UserRecord) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "department": user.department,
        "disabled": user.disabled,
        "must_change_password": user.must_change_password,
        "created_at": user.created_at,
        "last_login": user.last_login,
        "sessions": user.sessions,
    }


def _user(user: UserRecord) -> dict:
    return {"data": {"user": user_fields(user)}, "provenance": {}}


def _ok() -> dict:
    return {"data": {"ok": True}, "provenance": {}}


def _client(request: Request) -> str:
    """The address of the connection. Forwarding headers are the client's own words and are not read."""
    return request.client.host if request.client else "unknown"


def _with_session(payload: dict, token: str, state: AppState) -> JSONResponse:
    response = JSONResponse(payload)
    response.set_cookie(
        COOKIE,
        token,
        max_age=state.auth.session_seconds,
        path="/",
        httponly=True,
        samesite="lax",
        secure=state.settings.cookie_secure,
    )
    return response


# ── login ───────────────────────────────────────────────────────────────────
def _audit_refusal(
    state: AppState, event: str, exc: AuthError, *, actor: str | None, target: str | None, client: str
) -> None:
    if isinstance(exc, LockedError):
        # One line per lock: the log cannot be filled by repeating a refused login.
        if exc.first:
            detail = f"locked client={client}"
            state.audit.record(event, actor=actor, target=target, outcome="denied", detail=detail)
        return
    detail = f"{exc.code} client={client}"
    state.audit.record(event, actor=actor, target=target, outcome="failed", detail=detail)


def _login(state: AppState, body: LoginBody, client: str, previous: str) -> tuple[dict, str]:
    try:
        user = state.auth.authenticate(body.username, body.password, client)
        payload = viewer_payload(user.viewer, state)
        token = state.auth.open_session(user.id, expected_epoch=user.epoch)
    except AuthError as exc:
        # Only the name of an account that exists is written: people type passwords into the name field.
        known = exc.status < 500 and state.auth.find_username(body.username) is not None
        target = body.username if known else None
        _audit_refusal(state, "login", exc, actor=None, target=target, client=client)
        raise
    except PolicyError as exc:
        state.audit.record("login", actor=None, target=user.username, outcome="denied", detail=str(exc))
        raise
    if previous:
        state.auth.close_session(previous)  # the browser forgets it, so nobody could log it out
    state.audit.record("login", actor=user.username, target=user.username, detail=f"client={client}")
    return payload, token


@open_router.post("/auth/login")
async def login(body: LoginBody, request: Request, state: State) -> JSONResponse:
    payload, token = await state.run_password_work(
        _login, state, body, _client(request), session_token(request)
    )
    return _with_session(payload, token, state)


@router.post("/auth/logout")
def logout(request: Request, viewer: CurrentViewer, state: State) -> JSONResponse:
    state.auth.close_session(session_token(request))
    state.audit.record("logout", actor=viewer.username, target=viewer.username)
    response = JSONResponse(_ok())
    response.delete_cookie(
        COOKIE, path="/", httponly=True, samesite="lax", secure=state.settings.cookie_secure
    )
    return response


@router.get("/auth/me")
def me(viewer: CurrentViewer, state: State) -> dict:
    return viewer_payload(viewer, state)


def _change_password(state: AppState, viewer: Viewer, body: PasswordBody, client: str) -> tuple[dict, str]:
    try:
        user = state.auth.authenticate(viewer.username, body.current_password, client)
        if body.new_password == body.current_password:
            raise ApiError(422, "invalid_request", "지금과 다른 비밀번호로 정하세요.")
        changed = state.auth.set_password(
            user.id, body.new_password, must_change=False, expected_epoch=user.epoch
        )
        token = state.auth.open_session(user.id, expected_epoch=changed.epoch)
    except AuthError as exc:
        _audit_refusal(
            state, "password_change", exc, actor=viewer.username, target=viewer.username, client=client
        )
        if exc.status == 401:
            # Not 401: the session is valid, only the password typed is not.
            raise ApiError(400, "wrong_password", WRONG_CURRENT) from exc
        raise
    state.audit.record("password_change", actor=viewer.username, target=viewer.username)
    return viewer_payload(changed.viewer, state), token


@router.post("/auth/password")
async def change_password(
    body: PasswordBody, request: Request, viewer: CurrentViewer, state: State
) -> JSONResponse:
    """A new password ends every session of the account; this browser gets a new one."""
    payload, token = await state.run_password_work(_change_password, state, viewer, body, _client(request))
    return _with_session(payload, token, state)


# ── account management ──────────────────────────────────────────────────────
@admin_router.get("/users")
def users(state: State) -> dict:
    return {"data": {"users": [user_fields(u) for u in state.auth.list_users()]}, "provenance": {}}


def _create(state: AppState, admin: Viewer, body: CreateBody) -> UserRecord:
    user = state.auth.create_user(
        body.username,
        body.password,
        display_name=body.display_name,
        role=body.role,
        department=body.department,
        must_change=True,
    )
    detail = f"role={user.role} department={user.department}"
    state.audit.record("user_create", actor=admin.username, target=user.username, detail=detail)
    return user


@admin_router.post("/users")
async def create_user(body: CreateBody, admin: AdminViewer, state: State) -> dict:
    return _user(await state.run_password_work(_create, state, admin, body))


@admin_router.patch("/users/{user_id}")
def update_user(user_id: str, body: PatchBody, admin: AdminViewer, state: State) -> dict:
    sent = body.model_fields_set
    user = state.auth.update_user(
        user_id,
        role=body.role if "role" in sent else UNSET,
        department=body.department if "department" in sent else UNSET,
        disabled=body.disabled if "disabled" in sent else UNSET,
        display_name=body.display_name if "display_name" in sent else UNSET,
    )
    detail = (
        f"{','.join(sorted(sent))}: role={user.role} department={user.department} disabled={user.disabled}"
    )
    state.audit.record("user_update", actor=admin.username, target=user.username, detail=detail)
    return _user(user)


def _reset(state: AppState, admin: Viewer, user_id: str, password: str) -> UserRecord:
    user = state.auth.set_password(user_id, password, must_change=True)
    state.audit.record("user_password_reset", actor=admin.username, target=user.username)
    return user


@admin_router.post("/users/{user_id}/password")
async def reset_password(user_id: str, body: ResetBody, admin: AdminViewer, state: State) -> dict:
    return _user(await state.run_password_work(_reset, state, admin, user_id, body.password))


@admin_router.post("/users/{user_id}/logout")
def force_logout(user_id: str, admin: AdminViewer, state: State) -> dict:
    user = state.auth.get_user(user_id)
    ended = state.auth.revoke_sessions(user_id)
    detail = f"sessions={ended}"
    state.audit.record("user_logout", actor=admin.username, target=user.username, detail=detail)
    return _ok()


@admin_router.delete("/users/{user_id}")
def delete_user(user_id: str, admin: AdminViewer, state: State) -> dict:
    user = state.auth.delete_user(user_id)
    state.audit.record("user_delete", actor=admin.username, target=user.username)
    return _ok()


@admin_router.get("/audit")
def audit(state: State) -> dict:
    return {"data": {"entries": state.audit.recent()}, "provenance": {}}
