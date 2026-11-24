"""Account management and the audit log over HTTP (docs/auth-rbac.md §3)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from auth_helpers import (
    ACCOUNTS,
    COOKIE,
    DEPARTMENTS,
    PDF,
    Accounts,
    change_password,
    error_of,
    filled,
    login,
    password_of,
    routes_of,
    running_app,
    settings_in,
    shape,
)
from autorfx.rules import Ruleset
from contract_schema import assert_valid
from factories import make_package


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Accounts]:
    """Shared by the tests that change nothing."""
    with running_app(settings_in(tmp_path_factory.mktemp("admin")), ruleset) as accounts:
        yield accounts


@pytest.fixture
def small(tmp_path: Path, ruleset: Ruleset) -> Iterator[Accounts]:
    """An app of its own for tests that change accounts."""
    with running_app(settings_in(tmp_path), ruleset, names=("admin", "quality", "legal")) as accounts:
        yield accounts


# ── account management ──────────────────────────────────────────────────────
def admin_routes(app) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    found = [(m, p) for m, p in routes_of(app) if p.startswith("/admin") and m not in ("HEAD", "OPTIONS")]
    return sorted([*found, ("PUT", "/company")])


BODIES = {
    ("POST", "/admin/users"): {
        "username": "newcomer",
        "display_name": "New",
        "password": "temporary password",
        "role": "admin",
        "department": None,
    },
    ("PATCH", "/admin/users/{}"): {"role": "admin"},
    ("POST", "/admin/users/{}/password"): {"password": "temporary password"},
}


@pytest.mark.parametrize("name", DEPARTMENTS)
def test_members_cannot_manage_anything(api: Accounts, name: str):
    """auth-rbac §2: every /admin route and saving the company settings, for every department."""
    accounts, rfq_id = api, "rfq-000000000000"
    client = accounts[name]
    own = client.get("/auth/me").json()["data"]["viewer"]["user_id"]
    routes = admin_routes(accounts.app)
    assert len(routes) >= 8
    for method, path in routes:
        body = BODIES.get((method, shape(path)))
        if path == "/company":
            body = {"settings": {}, "profile": {}, "prices": []}
        response = client.request(method, filled(path, rfq_id, own), json=body)
        assert error_of(response) == (403, "forbidden"), (method, path)
    assert client.get("/auth/me").json()["data"]["viewer"]["role"] == "member"
    assert "newcomer" not in {u["username"] for u in users(accounts)}


def users(accounts: Accounts) -> list[dict]:
    body = accounts["admin"].get("/admin/users").json()
    assert_valid("admin-users", body)
    return body["data"]["users"]


def id_of(accounts: Accounts, username: str) -> str:
    return next(u["id"] for u in users(accounts) if u["username"] == username)


def patch(accounts: Accounts, username: str, **change: object):  # type: ignore[no-untyped-def]
    assert_valid("admin-user-patch-request", change)
    return accounts["admin"].patch(f"/admin/users/{id_of(accounts, username)}", json=change)


def test_the_account_list_has_no_secret(api: Accounts):
    accounts = api
    response = accounts["admin"].get("/admin/users")
    listed = {u["username"]: u for u in users(accounts)}
    assert set(listed) >= set(ACCOUNTS)
    assert listed["admin"]["sessions"] >= 1 and listed["admin"]["last_login"] is not None
    for secret in ("scrypt", "hash", "salt", accounts["admin"].cookies[COOKIE], password_of("quality")):
        assert secret not in response.text


def test_a_new_account_must_change_the_password_the_administrator_chose(small: Accounts):
    """auth-rbac §3."""
    body = {
        "username": "logi-2",
        "display_name": "둘째 물류",
        "password": "temporary password",
        "role": "member",
        "department": "logistics",
    }
    assert_valid("admin-user-create-request", body)
    created = small["admin"].post("/admin/users", json=body)
    assert created.status_code == 200, created.text
    assert_valid("admin-user", created.json())
    assert created.json()["data"]["user"]["must_change_password"] is True
    assert "temporary password" not in created.text
    client = small.anonymous()
    assert login(client, "logi-2", "temporary password").status_code == 200
    assert error_of(client.get("/meta")) == (403, "password_change_required")


@pytest.mark.parametrize(
    "change",
    [
        {"username": "quality"},
        {"username": "No Good"},
        {"department": "auditors"},
        {"department": None},
        {"role": "owner"},
        {"password": "short12"},
        {"display_name": ""},
        {"must_change_password": False},
    ],
)
def test_an_invalid_account_is_not_created(small: Accounts, change: dict):
    body = {
        "username": "newcomer",
        "display_name": "New",
        "password": "temporary password",
        "role": "member",
        "department": "legal",
        **change,
    }
    before = len(users(small))
    response = small["admin"].post("/admin/users", json=body)
    assert response.status_code in (400, 409, 422), response.text
    assert_valid("error", response.json())
    assert len(users(small)) == before and "temporary password" not in response.text


def test_disabling_an_account_works_on_the_next_request(small: Accounts):
    """auth-rbac §3."""
    client = small.fresh("quality")
    assert client.get("/auth/me").status_code == 200
    response = patch(small, "quality", disabled=True)
    assert response.status_code == 200, response.text
    assert_valid("admin-user", response.json())
    assert response.json()["data"]["user"]["disabled"] is True
    assert error_of(client.get("/auth/me")) == (401, "unauthenticated")
    assert login(small.anonymous(), "quality").status_code == 401
    patch(small, "quality", disabled=False)
    assert login(small.anonymous(), "quality").status_code == 200


def test_a_changed_department_and_role_work_on_the_next_request(small: Accounts):
    """auth-rbac §3: no new login is needed to lose a right."""
    client = small.fresh("quality")
    assert error_of(client.get("/company")) == (403, "forbidden")
    assert patch(small, "quality", department="costing").status_code == 200
    assert client.get("/auth/me").json()["data"]["viewer"]["department"] == "costing"
    assert client.get("/company").status_code == 200
    assert patch(small, "quality", department="legal").status_code == 200
    assert error_of(client.get("/company")) == (403, "forbidden")
    assert patch(small, "quality", role="admin", department=None).status_code == 200
    assert client.get("/admin/users").status_code == 200
    assert patch(small, "quality", role="member", department="quality").status_code == 200
    assert error_of(client.get("/admin/users")) == (403, "forbidden")


def test_a_patch_changes_only_what_it_names(small: Accounts):
    before = next(u for u in users(small) if u["username"] == "quality")
    assert patch(small, "quality", display_name="다른 이름").status_code == 200
    after = next(u for u in users(small) if u["username"] == "quality")
    assert {k for k in before if before[k] != after[k]} == {"display_name"}


@pytest.mark.parametrize(
    "change", [{"department": "auditors"}, {"department": None}, {"role": "owner"}, {"username": "x"}]
)
def test_an_invalid_change_is_refused(small: Accounts, change: dict):
    response = small["admin"].patch(f"/admin/users/{id_of(small, 'quality')}", json=change)
    assert response.status_code in (400, 409, 422)
    unchanged = next(u for u in users(small) if u["username"] == "quality")
    assert (unchanged["role"], unchanged["department"]) == ("member", "quality")


def test_a_reset_ends_the_sessions_and_asks_for_a_new_password(small: Accounts):
    """auth-rbac §3."""
    client = small.fresh("quality")
    path = f"/admin/users/{id_of(small, 'quality')}/password"
    response = small["admin"].post(path, json={"password": "reset by the admin"})
    assert response.status_code == 200 and "reset by the admin" not in response.text
    assert error_of(client.get("/auth/me")) == (401, "unauthenticated")
    again = small.anonymous()
    body = login(again, "quality", "reset by the admin").json()
    assert body["data"]["viewer"]["must_change_password"] is True


def test_a_forced_logout_ends_every_session_of_the_account(small: Accounts):
    clients = [small.fresh("quality"), small.fresh("quality")]
    other = small.fresh("legal")
    response = small["admin"].post(f"/admin/users/{id_of(small, 'quality')}/logout")
    assert response.status_code == 200
    assert_valid("ok", response.json())
    assert [c.get("/auth/me").status_code for c in clients] == [401, 401]
    assert other.get("/auth/me").status_code == 200


def test_a_deleted_account_is_gone(small: Accounts):
    client = small.fresh("quality")
    response = small["admin"].delete(f"/admin/users/{id_of(small, 'quality')}")
    assert response.status_code == 200, response.text
    assert_valid("ok", response.json())
    assert error_of(client.get("/auth/me")) == (401, "unauthenticated")
    assert login(small.anonymous(), "quality").status_code == 401
    assert "quality" not in {u["username"] for u in users(small)}


def test_an_unknown_account_is_not_found(small: Accounts):
    admin = small["admin"]
    assert admin.patch("/admin/users/nobody", json={"disabled": True}).status_code == 404
    assert admin.delete("/admin/users/nobody").status_code == 404
    assert admin.post("/admin/users/nobody/logout").status_code == 404


def test_the_last_administrator_cannot_be_removed(small: Accounts):
    """auth-rbac §3: not deleted, demoted or disabled; a second administrator makes it possible."""
    me = id_of(small, "admin")
    admin = small["admin"]
    attempts = (
        lambda: admin.delete(f"/admin/users/{me}"),
        lambda: patch(small, "admin", role="member", department="quality"),
        lambda: patch(small, "admin", disabled=True),
    )
    for attempt in attempts:
        response = attempt()
        assert 400 <= response.status_code < 500
        assert_valid("error", response.json())
        assert admin.get("/admin/users").status_code == 200
    assert patch(small, "legal", role="admin", department=None).status_code == 200
    assert admin.delete(f"/admin/users/{me}").status_code == 200


# ── audit ───────────────────────────────────────────────────────────────────
def test_the_audit_log_says_who_did_what_and_no_secret(small: Accounts, tmp_path: Path):
    """auth-rbac §3: no password, token, RFQ text or question text, in the file or in the response."""
    admin = small["admin"]
    question = "zitronenfalter unter der brücke?"
    patch(small, "quality", role="member", department="sales")
    uploader = small.fresh("quality")
    files = [("files", (p.name, p.read_bytes(), PDF)) for p in make_package(tmp_path / "upload")]
    rfq_id = uploader.post("/rfqs", files=files).json()["data"]["id"]
    small.state.wait()
    uploader.post(f"/rfqs/{rfq_id}/chat", json={"question": question})
    uploader.get(f"/rfqs/{rfq_id}/files/d01/source")
    failed = login(small.anonymous(client=("10.40.0.1", 1)), "legal", "a wrong password")
    assert failed.status_code == 401
    assert error_of(small["legal"].get("/admin/users")) == (403, "forbidden")
    change_password(small["legal"], password_of("legal"), "a brand new password")
    admin.post(f"/admin/users/{id_of(small, 'legal')}/password", json={"password": "reset by the admin"})

    response = admin.get("/admin/audit")
    assert_valid("audit", response.json())
    entries = response.json()["data"]["entries"]
    raw = (small.settings.work_dir / "_auth" / "audit.jsonl").read_text(encoding="utf-8")
    lines = [json.loads(line) for line in raw.splitlines()]
    assert entries and len(lines) >= len(entries)
    outcomes = {(e["actor"], e["outcome"]) for e in entries}
    assert ("admin", "ok") in outcomes
    assert any(e["outcome"] in ("failed", "denied") and "login" in e["event"] for e in entries)
    assert any(e["outcome"] == "denied" and e["actor"] == "legal" for e in entries), "the refused admin page"
    tokens = [c.cookies[COOKIE] for c in (admin, uploader, small["legal"]) if COOKIE in c.cookies]
    assert tokens
    for secret in (
        *tokens,
        password_of("admin"),
        password_of("legal"),
        "a wrong password",
        "a brand new password",
        "reset by the admin",
        question,
        "zitronenfalter",
        "1.2345",
        "target price",
        "seat brackets",
    ):
        assert secret not in raw and secret not in response.text, secret


def test_the_audit_log_shows_the_latest_two_hundred(small: Accounts):
    """auth-rbac §3: append only on disk, the newest 200 in the response."""
    client = small["legal"]
    for _ in range(210):
        assert client.get("/admin/users").status_code == 403
    entries = small["admin"].get("/admin/audit").json()["data"]["entries"]
    assert len(entries) == 200
    path = small.settings.work_dir / "_auth" / "audit.jsonl"
    before = path.read_bytes()
    assert len(before.splitlines()) >= 210
    small["admin"].post(f"/admin/users/{id_of(small, 'quality')}/logout")
    assert path.read_bytes().startswith(before), "nothing written earlier is changed"
