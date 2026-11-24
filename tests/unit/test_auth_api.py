"""Login, sessions and the gate over HTTP (docs/auth-rbac.md §3).

One thing is wrong per test: the gate tests without a session send a valid Origin, and the Origin tests
are logged in, because the design does not say which check comes first.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest

from auth_helpers import (
    ACCOUNTS,
    COOKIE,
    FULL,
    LIMITED,
    ORIGIN,
    PDF,
    Accounts,
    change_password,
    create_accounts,
    error_of,
    filled,
    login,
    make_client,
    open_store,
    password_of,
    routes_of,
    running_app,
    settings_in,
    shape,
    store_module,
)
from autorfx.analysis import RuleOnlyProvider
from autorfx.api import create_app
from autorfx.rules import Ruleset
from contract_schema import assert_valid
from factories import make_package, precost_inputs

OPEN = {("GET", "/health"), ("POST", "/auth/login")}
SELF = {("GET", "/auth/me"), ("POST", "/auth/password"), ("POST", "/auth/logout")}
UNSAFE = ("POST", "PUT", "PATCH", "DELETE")
EXPECTED = {
    ("GET", "/health"),
    ("GET", "/meta"),
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    ("GET", "/auth/me"),
    ("POST", "/auth/password"),
    ("GET", "/admin/users"),
    ("POST", "/admin/users"),
    ("PATCH", "/admin/users/{}"),
    ("DELETE", "/admin/users/{}"),
    ("POST", "/admin/users/{}/password"),
    ("POST", "/admin/users/{}/logout"),
    ("GET", "/admin/audit"),
    ("GET", "/rfqs"),
    ("POST", "/rfqs"),
    ("GET", "/rfqs/{}"),
    ("GET", "/rfqs/{}/job"),
    ("GET", "/rfqs/{}/clauses"),
    ("GET", "/rfqs/{}/rtm"),
    ("GET", "/rfqs/{}/rtm.xlsx"),
    ("GET", "/rfqs/{}/consistency"),
    ("GET", "/rfqs/{}/evaluation"),
    ("POST", "/rfqs/{}/chat"),
    ("GET", "/rfqs/{}/files/{}/source"),
    ("GET", "/rfqs/{}/files/{}/pages/{}/sentences"),
    ("GET", "/rfqs/{}/precost-setup"),
    ("POST", "/rfqs/{}/price-sheet/columns"),
    ("GET", "/price-sheet/words"),
    ("POST", "/price-sheet/words"),
    ("GET", "/company"),
    ("PUT", "/company"),
    ("POST", "/precost"),
}
"""Every path of the table in §3: a floor, so that the enumeration below cannot be empty."""
Api = tuple[Accounts, str]


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Api]:
    root = tmp_path_factory.mktemp("auth")
    with running_app(settings_in(root), ruleset) as accounts:
        yield accounts, accounts.upload(make_package(root / "upload"))


def cookie_of(response) -> str:  # type: ignore[no-untyped-def]
    return response.headers.get("set-cookie", "")


# ── the enumeration itself ──────────────────────────────────────────────────
def test_every_designed_path_exists_and_is_enumerated(api: Api):
    """auth-rbac §3: the table of paths; the gate tests below walk the same list."""
    accounts, _ = api
    found = {(m, shape(p)) for m, p in routes_of(accounts.app)}
    assert found >= EXPECTED, EXPECTED - found
    documented = {
        (method.upper(), shape(path))
        for path, operations in accounts.app.openapi()["paths"].items()
        for method in operations
    }
    assert documented <= found, "the walk reaches the routes of included routers"


# ── without a session ───────────────────────────────────────────────────────
def test_every_route_but_two_needs_a_session(api: Api):
    """auth-rbac §1.2, §3: deny by default, for routes that exist today and for new ones."""
    accounts, rfq_id = api
    client = accounts.anonymous()
    checked = 0
    for method, path in sorted(routes_of(accounts.app) - OPEN):
        if method in ("HEAD", "OPTIONS"):
            continue
        response = client.request(method, filled(path, rfq_id))
        assert error_of(response) == (401, "unauthenticated"), (method, path)
        checked += 1
    assert checked >= len(EXPECTED) - len(OPEN)


def test_head_requests_need_a_session_too(api: Api):
    accounts, rfq_id = api
    client = accounts.anonymous()
    for path in ("/meta", f"/rfqs/{rfq_id}/files/d01/source", f"/rfqs/{rfq_id}/rtm.xlsx"):
        assert client.head(path).status_code == 401


@pytest.mark.parametrize("path", ["/nowhere", "/docs", "/META", "/health/x"])
def test_an_unknown_address_is_not_told_apart_without_a_session(api: Api, path: str):
    """auth-rbac §3: the gate is in front of the routes, so it also covers what is not a route."""
    assert error_of(api[0].anonymous().get(path)) == (401, "unauthenticated")


@pytest.mark.parametrize("value", ["", "x", "0" * 43, "../../etc", "%00", "null"])
def test_a_made_up_cookie_is_nobody(api: Api, value: str):
    accounts, rfq_id = api
    client = accounts.anonymous()
    response = client.get(f"/rfqs/{rfq_id}/clauses", headers={"Cookie": f"{COOKIE}={value}"})
    assert error_of(response) == (401, "unauthenticated")


def test_the_cookie_is_the_only_credential(api: Api):
    """auth-rbac §1.1: the session token works in the cookie and nowhere else."""
    accounts, rfq_id = api
    token = accounts["quality"].cookies[COOKIE]
    client = accounts.anonymous()
    for headers in ({"Authorization": f"Bearer {token}"}, {"X-Session": token}, {"X-User": "admin"}):
        assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.get("/auth/me", params={COOKIE: token}).status_code == 401
    assert client.get("/auth/me", headers={"Cookie": f"{COOKIE}={token}"}).status_code == 200


def test_the_two_open_paths_are_open(api: Api):
    client = api[0].anonymous()
    assert client.get("/health").json() == {"status": "ok"}
    assert login(client, "quality").status_code == 200


def test_the_api_description_is_not_served(api: Api):
    """auth-rbac §3: not even to an administrator."""
    admin = api[0]["admin"]
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert error_of(admin.get(path)) == (404, "not_found"), path
    paths = {path for _, path in routes_of(api[0].app)}
    assert not paths & {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


# ── origin ──────────────────────────────────────────────────────────────────
def test_every_unsafe_request_needs_the_web_apps_origin(api: Api):
    """auth-rbac §3: CSRF: missing, foreign or look-alike origins are refused on every unsafe route."""
    accounts, rfq_id = api
    client = accounts.fresh("admin")
    del client.headers["Origin"]
    unsafe = [(m, p) for m, p in sorted(routes_of(accounts.app)) if m in UNSAFE]
    assert len(unsafe) >= sum(m in UNSAFE for m, _ in EXPECTED)
    for method, path in unsafe:
        for origin in (
            None,
            "https://example.org",
            "null",
            f"{ORIGIN}.example.org",
            ORIGIN.upper(),
            f"{ORIGIN}/",
        ):
            headers = {"Origin": origin} if origin else {}
            response = client.request(method, filled(path, rfq_id), headers=headers)
            assert error_of(response) == (403, "bad_origin"), (method, path, origin)
    assert client.get("/auth/me").status_code == 200, "nothing of it logged the account out"


def test_login_and_uploads_are_refused_from_another_origin(api: Api, tmp_path: Path):
    """auth-rbac §3: the same for login (no session yet) and for multipart, which needs no preflight."""
    accounts, _ = api
    stranger = accounts.anonymous(origin="https://example.org")
    response = login(stranger, "quality")
    assert error_of(response) == (403, "bad_origin") and not cookie_of(response)
    sender = accounts.fresh("sales")
    sender.headers["Origin"] = "https://example.org"
    files = [("files", (p.name, p.read_bytes(), PDF)) for p in make_package(tmp_path)]
    assert error_of(sender.post("/rfqs", files=files)) == (403, "bad_origin")


def test_reading_needs_no_origin(api: Api):
    """A browser sends no Origin on same-site GET navigation and downloads."""
    accounts, rfq_id = api
    client = accounts.fresh("quality")
    del client.headers["Origin"]
    assert client.get(f"/rfqs/{rfq_id}/clauses").status_code == 200


# ── login and the cookie ────────────────────────────────────────────────────
def test_login_returns_the_viewer_and_sets_the_cookie(api: Api):
    """auth-rbac §3: HttpOnly, SameSite=Lax, Path=/, no Domain; the token is not in the body."""
    client = api[0].anonymous()
    response = login(client, "logistics")
    assert_valid("viewer", response.json())
    viewer = response.json()["data"]["viewer"]
    assert (viewer["username"], viewer["role"], viewer["department"]) == ("logistics", "member", "logistics")
    cookie = cookie_of(response)
    attributes = {part.strip().casefold() for part in cookie.split(";")[1:]}
    assert cookie.startswith(f"{COOKIE}=")
    assert {"httponly", "samesite=lax", "path=/"} <= attributes
    assert not any(a.startswith("domain") for a in attributes) and "secure" not in attributes
    assert client.cookies[COOKIE] not in response.text and password_of("logistics") not in response.text


def test_the_cookie_is_secure_when_the_server_says_so(
    tmp_path: Path, ruleset: Ruleset, monkeypatch: pytest.MonkeyPatch
):
    """auth-rbac §3: AUTORFX_COOKIE_SECURE for HTTPS."""
    monkeypatch.setenv("AUTORFX_COOKIE_SECURE", "true")
    with running_app(settings_in(tmp_path), ruleset, names=("quality",)) as accounts:
        response = login(accounts.anonymous(), "quality")
    assert "secure" in {part.strip().casefold() for part in cookie_of(response).split(";")}


def test_the_session_length_is_a_setting_of_twelve_hours(tmp_path: Path):
    """auth-rbac §3: AUTORFX_SESSION_HOURS, 12 unless set (the expiry itself: test_auth_store.py)."""
    settings = settings_in(tmp_path)
    by_alias = {str(f.validation_alias): name for name, f in type(settings).model_fields.items()}
    assert getattr(settings, by_alias["AUTORFX_SESSION_HOURS"]) == 12
    assert getattr(settings, by_alias["AUTORFX_COOKIE_SECURE"]) is False


@pytest.mark.parametrize("name", ACCOUNTS)
def test_the_viewer_says_what_the_account_may_do(api: Api, ruleset: Ruleset, name: str):
    """auth-rbac §2, §3: the web reads its switches from here."""
    body = api[0][name].get("/auth/me").json()
    assert_valid("viewer", body)
    viewer = body["data"]["viewer"]
    label = None if name == "admin" else ruleset.department(name).label
    assert viewer["department_label"] == label and viewer["display_name"] == f"{name} tester"
    assert viewer["group"] == (
        "cost_visible" if name in ("admin", "sales", "purchasing", "costing") else "cost_restricted"
    )
    assert (viewer["full_scope"], viewer["can_upload"]) == (name in FULL, name in FULL)
    assert viewer["can_manage"] is (name == "admin") and viewer["must_change_password"] is False


def test_a_wrong_password_and_an_unknown_user_look_the_same(api: Api):
    """auth-rbac §3."""
    answers = []
    for username in ("quality", "nobody-at-all"):
        client = api[0].anonymous(client=("10.20.0.1", 1))
        response = login(client, username, "not the password")
        assert_valid("error", response.json())
        assert not cookie_of(response) and COOKIE not in client.cookies
        answers.append((response.status_code, response.json()))
    assert answers[0] == answers[1] and answers[0][0] == 401


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"username": "quality"},
        {"username": "quality", "password": ""},
        {"username": "quality", "password": "x" * 129},
        {"username": "quality", "password": "x" * 20, "role": "admin"},
        {"username": ["quality"], "password": "x" * 20},
        {"username": "q" * 65, "password": "x" * 20},
    ],
)
def test_a_malformed_login_is_refused(api: Api, body: dict):
    response = api[0].anonymous().post("/auth/login", json=body)
    assert error_of(response) == (422, "invalid_request")
    assert "x" * 20 not in response.text, "the input is not repeated"


def test_the_lock_follows_the_connection_not_a_header(api: Api):
    """auth-rbac §3: the address is ``request.client.host``; a forwarded header neither hides the guesser
    nor locks out another address."""
    accounts, _ = api
    guesser = accounts.anonymous(client=("10.30.0.1", 1))
    for number in range(5):
        guesser.headers["X-Forwarded-For"] = guesser.headers["X-Real-IP"] = f"172.16.0.{number}"
        assert login(guesser, "legal", "a wrong password").status_code == 401
    assert login(guesser, "legal").status_code != 200, "locked, although the header changed every time"
    victim = accounts.anonymous(client=("10.30.0.2", 1))
    victim.headers["X-Forwarded-For"] = "10.30.0.1"
    assert login(victim, "legal").status_code == 200


def test_a_session_chosen_by_the_client_is_never_adopted(api: Api):
    """Security: session fixation."""
    accounts, _ = api
    planted = "planted-session-token-0123456789-0123456789-abc"
    client = accounts.anonymous()
    response = client.post(
        "/auth/login",
        json={"username": "quality", "password": password_of("quality")},
        headers={"Cookie": f"{COOKIE}={planted}"},
    )
    assert response.status_code == 200
    issued = cookie_of(response).split(";")[0].split("=", 1)[1]
    assert issued and issued != planted
    other = accounts.anonymous()
    assert other.get("/auth/me", headers={"Cookie": f"{COOKIE}={planted}"}).status_code == 401


def test_logging_in_over_an_existing_session_gives_a_new_one(api: Api):
    """Security: another account's live session, planted in the browser, is not upgraded or reused."""
    accounts, _ = api
    mine = accounts.fresh("legal").cookies[COOKIE]
    response = accounts.anonymous().post(
        "/auth/login",
        json={"username": "quality", "password": password_of("quality")},
        headers={"Cookie": f"{COOKIE}={mine}"},
    )
    issued = cookie_of(response).split(";")[0].split("=", 1)[1]
    assert response.status_code == 200 and issued != mine
    me = accounts.anonymous().get("/auth/me", headers={"Cookie": f"{COOKIE}={mine}"})
    assert me.status_code == 401 or me.json()["data"]["viewer"]["username"] == "legal"


def test_logout_ends_the_session_on_the_server(api: Api):
    """auth-rbac §3: the cookie is deleted and the token is worthless afterwards."""
    client = api[0].fresh("quality")
    token = client.cookies[COOKIE]
    response = client.post("/auth/logout")
    assert response.status_code == 200
    assert_valid("ok", response.json())
    assert cookie_of(response).startswith(f"{COOKIE}=") and COOKIE not in client.cookies
    replay = api[0].anonymous().get("/auth/me", headers={"Cookie": f"{COOKIE}={token}"})
    assert error_of(replay) == (401, "unauthenticated")


# ── password change ─────────────────────────────────────────────────────────
@pytest.fixture
def small(tmp_path: Path, ruleset: Ruleset) -> Iterator[Accounts]:
    """An app of its own for tests that change accounts."""
    with running_app(settings_in(tmp_path), ruleset, names=("admin", "quality", "legal")) as accounts:
        yield accounts


def test_changing_the_password_rotates_this_session_and_ends_the_others(small: Accounts):
    """auth-rbac §3."""
    here, there = small.fresh("quality"), small.fresh("quality")
    old = here.cookies[COOKIE]
    response = change_password(here, password_of("quality"), "a brand new password")
    assert response.status_code == 200, response.text
    assert here.cookies[COOKIE] != old and here.get("/auth/me").status_code == 200
    assert there.get("/auth/me").status_code == 401
    assert small.anonymous().get("/auth/me", headers={"Cookie": f"{COOKIE}={old}"}).status_code == 401
    assert login(small.anonymous(), "quality").status_code == 401
    assert login(small.anonymous(), "quality", "a brand new password").status_code == 200


def test_the_current_password_is_asked_for(small: Accounts):
    """Security: a borrowed browser cannot take the account over."""
    here, there = small.fresh("quality"), small.fresh("quality")
    response = change_password(here, "not the password", "a brand new password")
    assert 400 <= response.status_code < 500 and response.status_code != 401
    assert_valid("error", response.json())
    assert here.get("/auth/me").status_code == there.get("/auth/me").status_code == 200
    assert login(small.anonymous(), "quality").status_code == 200


@pytest.mark.parametrize("new", ["", "short12", "x" * 129])
def test_a_new_password_outside_the_limits_is_refused(small: Accounts, new: str):
    response = change_password(small["quality"], password_of("quality"), new)
    assert error_of(response) == (422, "invalid_request")
    assert login(small.anonymous(), "quality").status_code == 200


def test_an_account_that_must_change_its_password_can_do_nothing_else(tmp_path: Path, ruleset: Ruleset):
    """auth-rbac §3: every route but me, password and logout answers 403 until it has been changed."""
    settings = settings_in(tmp_path)
    create_accounts(settings, ruleset, ("admin",), must_change=True)
    with running_app(settings, ruleset, create=False) as accounts:
        client = accounts["admin"]
        me = client.get("/auth/me").json()
        assert_valid("viewer", me)
        assert me["data"]["viewer"]["must_change_password"] is True
        for method, path in sorted(routes_of(accounts.app) - OPEN - SELF):
            if method in ("HEAD", "OPTIONS"):
                continue
            response = client.request(method, filled(path, "rfq-000000000000"))
            assert error_of(response) == (403, "password_change_required"), (method, path)
        assert client.get("/health").status_code == 200
        assert change_password(client, password_of("admin"), "a brand new password").status_code == 200
        assert client.get("/admin/users").status_code == 200
        assert client.get("/auth/me").json()["data"]["viewer"]["must_change_password"] is False


def test_an_account_that_must_change_its_password_can_log_out(tmp_path: Path, ruleset: Ruleset):
    settings = settings_in(tmp_path)
    create_accounts(settings, ruleset, ("quality",), must_change=True)
    with running_app(settings, ruleset, create=False) as accounts:
        assert accounts["quality"].post("/auth/logout").status_code == 200


# ── what a request may claim ────────────────────────────────────────────────
DATA_PATHS = (
    "/meta",
    "/auth/me",
    "/rfqs",
    "/rfqs/{rfq_id}",
    "/rfqs/{rfq_id}/job",
    "/rfqs/{rfq_id}/clauses",
    "/rfqs/{rfq_id}/rtm",
    "/rfqs/{rfq_id}/consistency",
    "/rfqs/{rfq_id}/evaluation",
    "/rfqs/{rfq_id}/files/d01/pages/1/sentences",
    "/rfqs/{rfq_id}/files/d01/source",
    "/rfqs/{rfq_id}/precost-setup",
    "/company",
    "/price-sheet/words",
)


@pytest.mark.parametrize("name", LIMITED)
def test_a_team_in_the_query_changes_nothing(api: Api, name: str):
    """auth-rbac §1.1: the identity comes from the session only."""
    accounts, rfq_id = api
    client = accounts[name]
    for path in DATA_PATHS:
        url = filled(path, rfq_id)
        plain = client.get(url)
        for claim in ("costing", "sales", "admin"):
            claimed = client.get(url, params={"team": claim, "role": "admin", "group": "cost_visible"})
            assert claimed.status_code == plain.status_code, (path, claim)
            assert claimed.content == plain.content, (path, claim)


def test_a_team_in_the_body_is_refused(api: Api):
    """auth-rbac §1.1: not ignored: a client that still sends one is wrong and is told so."""
    accounts, rfq_id = api
    chat = accounts["quality"].post(
        f"/rfqs/{rfq_id}/chat", json={"team": "costing", "question": "인도조건은?"}
    )
    assert error_of(chat) == (422, "invalid_request")
    body = {"rfq_id": None, "inputs": precost_inputs(), "resolved": {}}
    assert error_of(accounts["sales"].post("/precost", json={**body, "team": "sales"})) == (
        422,
        "invalid_request",
    )
    assert accounts["sales"].post("/precost", json=body).status_code == 200


@pytest.mark.parametrize("name", [n for n in ACCOUNTS if n not in FULL])
def test_only_admin_and_sales_may_upload(api: Api, tmp_path: Path, name: str):
    """auth-rbac §2."""
    files = [("files", (p.name, p.read_bytes(), PDF)) for p in make_package(tmp_path)]
    assert error_of(api[0][name].post("/rfqs", files=files)) == (403, "forbidden")


@pytest.mark.parametrize("name", FULL)
def test_admin_and_sales_can_upload(api: Api, tmp_path: Path, name: str):
    files = [("files", (p.name, p.read_bytes(), PDF)) for p in make_package(tmp_path)]
    assert api[0][name].post("/rfqs", files=files).status_code == 200
    api[0].state.wait()


# ── headers ─────────────────────────────────────────────────────────────────
def test_no_response_may_be_stored(api: Api):
    """auth-rbac §3: every response depends on the cookie: data, errors, refusals and the open paths."""
    accounts, rfq_id = api
    admin, nobody = accounts["admin"], accounts.anonymous()
    bad = accounts.fresh("quality")
    bad.headers["Origin"] = "https://example.org"
    responses = [
        nobody.get("/health"),
        nobody.get("/meta"),
        nobody.get("/nowhere"),
        login(nobody, "nobody-x", "not the password"),
        login(nobody, "quality"),
        bad.post("/auth/logout"),
        accounts["quality"].get("/admin/users"),
        admin.get("/nowhere"),
        admin.get("/rfqs/rfq-doesnotexist/clauses"),
        admin.post("/auth/password", json={}),
        *(admin.get(filled(path, rfq_id)) for path in DATA_PATHS),
        admin.get(f"/rfqs/{rfq_id}/rtm.xlsx"),
        accounts["quality"].get(f"/rfqs/{rfq_id}/files/d01/source"),
    ]
    for response in responses:
        where = (response.request.method, response.request.url.path, response.status_code)
        assert response.headers["cache-control"] == "no-store", where
        vary = {part.strip().casefold() for part in response.headers["vary"].split(",")}
        assert "cookie" in vary, where
        assert response.headers["x-content-type-options"] == "nosniff", where


def test_a_refusal_can_be_read_by_the_web_app_and_by_no_other_site(api: Api):
    """auth-rbac §3: the gate is inside CORS: otherwise the browser hides the 401 and the app cannot show
    the login form."""
    accounts, rfq_id = api
    client = accounts.anonymous(origin=None)
    for path in ("/auth/me", f"/rfqs/{rfq_id}/clauses"):
        allowed = client.get(path, headers={"Origin": ORIGIN})
        assert allowed.status_code == 401
        assert allowed.headers["access-control-allow-origin"] == ORIGIN
        assert allowed.headers["access-control-allow-credentials"] == "true"
        foreign = client.get(path, headers={"Origin": "https://example.org"})
        assert foreign.status_code == 401 and "access-control-allow-origin" not in foreign.headers
    data = accounts["quality"].get("/auth/me", headers={"Origin": "https://example.org"})
    assert "access-control-allow-origin" not in data.headers


# ── a broken store ──────────────────────────────────────────────────────────
def test_a_corrupt_store_lets_nobody_in(tmp_path: Path, ruleset: Ruleset):
    """auth-rbac §1.2: the server refuses (at start, or 500/503); a login never succeeds, an old cookie
    is worthless, and no new account store appears in its place."""
    settings = settings_in(tmp_path)
    create_accounts(settings, ruleset, ("quality",))
    token = open_store(settings, ruleset).open_session(
        next(u.id for u in open_store(settings, ruleset).list_users())
    )
    folder = settings.work_dir / "_auth"
    for path in folder.glob("auth.sqlite*"):
        path.write_bytes(b"garbage " * 1024)
    app = create_app(settings, ruleset=ruleset, provider=RuleOnlyProvider())
    seen: list[int] = []
    try:
        with make_client(app, raise_server_exceptions=False) as client:
            response = login(client, "quality")
            assert not cookie_of(response)
            seen.append(response.status_code)
            seen.append(client.get("/meta", headers={"Cookie": f"{COOKIE}={token}"}).status_code)
            assert client.get("/health").status_code == 200
    except AssertionError:
        raise
    except Exception as exc:  # the server did not start: nobody is in
        assert not isinstance(exc, (ImportError, AttributeError)), exc
        return
    assert seen[0] in (500, 503) and seen[1] in (401, 500, 503)


def test_a_deleted_store_does_not_open_the_server(tmp_path: Path, ruleset: Ruleset):
    """Fail closed: an empty store has no accounts, so every old cookie and password is refused."""
    import shutil

    settings = settings_in(tmp_path)
    create_accounts(settings, ruleset, ("quality",))
    store = open_store(settings, ruleset)
    token = store.open_session(store.list_users()[0].id)
    shutil.rmtree(settings.work_dir / "_auth")
    with running_app(settings, ruleset, create=False) as accounts:
        assert login(accounts.anonymous(), "quality").status_code == 401
        client = accounts.anonymous()
        assert client.get("/meta", headers={"Cookie": f"{COOKIE}={token}"}).status_code == 401


def test_the_auth_folder_is_not_an_rfq(api: Api):
    """The account files live beside the RFQs; no RFQ address reaches them."""
    accounts, _ = api
    for rfq_id in ("_auth", "..%2F_auth", "_auth%2Faudit.jsonl"):
        assert accounts["admin"].get(f"/rfqs/{rfq_id}/job").status_code == 404
    listed = accounts["admin"].get("/rfqs").json()
    assert_valid("rfq-list", listed)
    assert all(r["id"].startswith("rfq-") for r in listed["data"]["rfqs"])


def test_an_upload_is_not_kept_after_a_refusal(api: Api, tmp_path: Path):
    """The bytes of a refused upload are not left on the server."""
    accounts, _ = api
    files = [("files", ("intruder.pdf", io.BytesIO(b"%PDF-1.7 intruder"), PDF))]
    assert accounts["quality"].post("/rfqs", files=files).status_code == 403
    assert not list(accounts.settings.data_dir.rglob("intruder*"))


def test_the_store_module_exposes_the_designed_names():
    """auth-rbac §3: the interface src and tests agreed on."""
    for name in (
        "create_user",
        "list_users",
        "get_user",
        "update_user",
        "set_password",
        "authenticate",
        "open_session",
        "viewer_for",
        "close_session",
        "revoke_sessions",
        "secret",
    ):
        assert callable(getattr(store_module().AuthStore, name))
