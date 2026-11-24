"""Accounts, logged-in clients and scopes for tests of the login and the department scope.

``autorfx.auth`` is imported inside the functions: while a module is missing, the tests that need it
fail one by one and every other test file still runs.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from types import ModuleType

import httpx
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from autorfx.analysis import AnalysisProvider, RuleOnlyProvider
from autorfx.api import create_app
from autorfx.config import Settings
from autorfx.models import Clause, ClauseAnalysis
from autorfx.rules import Ruleset
from contract_schema import assert_valid

BASE_URL = "http://localhost"
ORIGIN = "http://localhost:3000"
COOKIE = "autorfx_session"
PDF = "application/pdf"
DEPARTMENTS = (
    "sales",
    "development",
    "quality",
    "production_engineering",
    "purchasing",
    "costing",
    "logistics",
    "legal",
)
ACCOUNTS = ("admin", *DEPARTMENTS)
"""One administrator and one member per rule department (docs/auth-rbac.md §2)."""
FULL = ("admin", "sales")
COST_VISIBLE = ("admin", "sales", "purchasing", "costing")
LIMITED = tuple(name for name in ACCOUNTS if name not in FULL)
SECRET = b"test-secret-for-opaque-ids-0123456789"


def policy() -> ModuleType:
    return import_module("autorfx.auth.policy")


def store_module() -> ModuleType:
    return import_module("autorfx.auth.store")


def password_of(username: str) -> str:
    return f"pw-{username}-0f3a9c"


# ── scopes ──────────────────────────────────────────────────────────────────
def viewer(name: str, **change: object):  # type: ignore[no-untyped-def]
    """The viewer of a seeded account, without a store."""
    fields = {
        "user_id": f"u-{name}",
        "username": name,
        "display_name": name,
        "role": "admin" if name == "admin" else "member",
        "department": None if name == "admin" else name,
        "must_change_password": False,
    }
    return policy().Viewer(**{**fields, **change})


def scope_for(name: str, ruleset: Ruleset):  # type: ignore[no-untyped-def]
    """The scope of a seeded account: what ``scope_of`` gives for its role and department."""
    return policy().scope_of(viewer(name), ruleset)


def cost_restricted_scope(ruleset: Ruleset):  # type: ignore[no-untyped-def]
    """A scope that differs from the full one only in cost visibility (every department, no cost).

    No seeded account has it: it isolates the cost rule from the department rule, which is what the
    ``cost_restricted`` group used to stand for.
    """
    return policy().Scope(cost_visible=False, areas=frozenset(ruleset.department_ids))


def visible_of(
    clauses: Sequence[Clause], analyses: Sequence[ClauseAnalysis], scope: object, ruleset: Ruleset
) -> tuple[Clause, ...]:
    from autorfx import access

    return access.visible_clauses(clauses, analyses, scope, ruleset)  # type: ignore[attr-defined]


# ── accounts and clients ────────────────────────────────────────────────────
def settings_in(folder: Path, **change: object) -> Settings:
    return Settings(_env_file=None, data_dir=folder / "data", **change)  # type: ignore[arg-type]


def open_store(settings: Settings, ruleset: Ruleset, **options: object):  # type: ignore[no-untyped-def]
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    return store_module().AuthStore(
        settings.work_dir, department_ids=tuple(ruleset.department_ids), **options
    )


def create_accounts(
    settings: Settings,
    ruleset: Ruleset,
    names: Sequence[str] = ACCOUNTS,
    *,
    must_change: bool = False,
) -> dict[str, object]:
    """One account per name through ``AuthStore.create_user``; returns the records by name."""
    store = open_store(settings, ruleset)
    return {
        name: store.create_user(
            name,
            password_of(name),
            display_name=f"{name} tester",
            role="admin" if name == "admin" else "member",
            department=None if name == "admin" else name,
            must_change=must_change,
        )
        for name in names
    }


def make_client(app: FastAPI, *, origin: str | None = ORIGIN, **options: object) -> TestClient:
    """A browser: its own cookie jar, the web app's origin. It does not start the app."""
    headers = {"Origin": origin} if origin else {}
    return TestClient(app, base_url=BASE_URL, headers=headers, **options)  # type: ignore[arg-type]


def login(client: TestClient, username: str, password: str | None = None) -> httpx.Response:
    """The real ``POST /auth/login``; the session cookie stays in the client's jar."""
    body = {"username": username, "password": password or password_of(username)}
    assert_valid("login-request", body)
    return client.post("/auth/login", json=body)


@dataclass
class Accounts:
    """One running app and one client per account."""

    app: FastAPI
    settings: Settings
    clients: dict[str, TestClient] = field(default_factory=dict)

    def anonymous(self, **options: object) -> TestClient:
        return make_client(self.app, **options)  # type: ignore[arg-type]

    def fresh(self, username: str, **options: object) -> TestClient:
        """A new session of this account in a browser of its own."""
        client = make_client(self.app, **options)  # type: ignore[arg-type]
        response = login(client, username)
        assert response.status_code == 200, response.text
        return client

    def __getitem__(self, username: str) -> TestClient:
        if username not in self.clients:
            self.clients[username] = self.fresh(username)
        return self.clients[username]

    @property
    def state(self):  # type: ignore[no-untyped-def]
        return self.app.state.autorfx

    def upload(self, sources: Sequence[Path], *, username: str = "sales") -> str:
        """Upload a package and wait for its job; returns the RFQ id."""
        files = [("files", (p.name, p.read_bytes(), PDF)) for p in sources]
        response = self[username].post("/rfqs", files=files)
        assert response.status_code == 200, response.text
        self.state.wait()
        return response.json()["data"]["id"]


@contextmanager
def running_app(
    settings: Settings,
    ruleset: Ruleset,
    *,
    names: Sequence[str] = ACCOUNTS,
    provider: AnalysisProvider | None = None,
    create: bool = True,
) -> Iterator[Accounts]:
    """The app with its accounts. Only one client runs the lifespan; the others share the app."""
    if create:
        create_accounts(settings, ruleset, names)
    app = create_app(settings, ruleset=ruleset, provider=provider or RuleOnlyProvider())
    with make_client(app):
        yield Accounts(app, settings)


def error_of(response: httpx.Response) -> tuple[int, str]:
    body = response.json()
    assert_valid("error", body)
    return response.status_code, body["error"]["code"]


def change_password(client: TestClient, current: str, new: str) -> httpx.Response:
    return client.post("/auth/password", json={"current_password": current, "new_password": new})


# ── routes ──────────────────────────────────────────────────────────────────
def routes_of(app: FastAPI) -> set[tuple[str, str]]:
    """(method, path) of every route, included routers too; a new route is covered without a change here."""
    return {
        (method, context.path)
        for context in iter_route_contexts(app.routes)
        if context.path
        for method in (context.methods or ())
    }


def shape(path: str) -> str:
    parts = ["{}" if part.startswith("{") else part for part in path.split("/")]
    return "/".join(parts)


def filled(path: str, rfq_id: str, user_id: str = "someone") -> str:
    """A path with real values, so that only the gate can refuse it."""
    out = []
    for part in path.split("/"):
        if not part.startswith("{"):
            out.append(part)
        elif "rfq" in part:
            out.append(rfq_id)
        elif "file" in part:
            out.append("d01")
        elif "page" in part:
            out.append("1")
        else:
            out.append(user_id)
    return "/".join(out)
