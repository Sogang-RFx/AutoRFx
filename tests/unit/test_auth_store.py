"""Accounts and sessions on disk (docs/auth-rbac.md §3): hashing, lockout, expiry, revocation, fail closed."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import stat
import threading
from collections.abc import Callable, Collection, Mapping
from importlib import import_module
from pathlib import Path

import pytest

from auth_helpers import DEPARTMENTS, store_module

PASSWORD = "correct horse 42"
HOUR = 3600.0
REAL_SCRYPT = hashlib.scrypt


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def patch_scrypt(monkeypatch: pytest.MonkeyPatch, replacement: Callable) -> None:
    """Replace scrypt wherever the password module takes it from."""
    passwords = import_module("autorfx.auth.passwords")
    monkeypatch.setattr(hashlib, "scrypt", replacement)
    if hasattr(passwords, "scrypt"):
        monkeypatch.setattr(passwords, "scrypt", replacement)


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cheap stand-in for scrypt, for tests that try many passwords."""

    def cheap(password, *, salt, n=0, r=0, p=0, maxmem=0, dklen=64):  # type: ignore[no-untyped-def]
        return hashlib.pbkdf2_hmac("sha256", password, salt, 1, dklen)

    patch_scrypt(monkeypatch, cheap)


def open_store(folder: Path, **options: object):  # type: ignore[no-untyped-def]
    folder.mkdir(parents=True, exist_ok=True)
    return store_module().AuthStore(folder, department_ids=DEPARTMENTS, **options)


@pytest.fixture
def store(tmp_path: Path, clock: Clock):  # type: ignore[no-untyped-def]
    return open_store(tmp_path / "work", now=clock)


def add(store, username: str = "quality", **change: object):  # type: ignore[no-untyped-def]
    fields = {
        "display_name": "Tester",
        "role": "member",
        "department": "quality",
        "must_change": False,
    }
    password = change.pop("password", PASSWORD)
    return store.create_user(username, password, **{**fields, **change})


def add_admin(store, username: str = "admin"):  # type: ignore[no-untyped-def]
    return add(store, username, role="admin", department=None)


def auth_files(work: Path) -> list[Path]:
    return [p for p in (work / "_auth").rglob("*") if p.is_file()]


def refused(store, username: str, password: str, client: str):  # type: ignore[no-untyped-def]
    with pytest.raises(store_module().AuthError) as caught:
        store.authenticate(username, password, client)
    return caught.value


# ── passwords ───────────────────────────────────────────────────────────────
def test_passwords_are_hashed_with_the_agreed_scrypt_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """auth-rbac §3: scrypt n=2**15, r=8, p=3, maxmem stated, a fresh salt per password."""
    calls: list[dict] = []

    def spy(password, **options):  # type: ignore[no-untyped-def]
        calls.append(options)
        return REAL_SCRYPT(password, **options)

    patch_scrypt(monkeypatch, spy)
    store = open_store(tmp_path)
    add(store, "one")
    add(store, "two")
    salts = {bytes(c["salt"]) for c in calls}
    assert len(salts) >= 2 and all(len(salt) >= 16 for salt in salts), "a salt of its own per account"
    for call in calls:
        assert (call["n"], call["r"], call["p"]) == (2**15, 8, 3)
        assert call["maxmem"] >= 128 * 2**15 * 8, "the default limit of 32 MiB is too small for these"


def test_the_parameters_are_stored_with_the_hash(tmp_path: Path):
    """auth-rbac §3: a later change of the parameters does not lock the stored accounts out."""
    store = open_store(tmp_path)
    add(store)
    db = sqlite3.connect(next(p for p in auth_files(tmp_path) if p.name == "auth.sqlite"))
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    rows = [row for t in tables for row in db.execute(f'SELECT * FROM "{t}"') if "quality" in row]
    db.close()
    assert rows, "the account row"
    cells = " ".join(str(cell) for cell in rows[0])
    for parameter in (r"(?<!\d)(32768|15)(?!\d)", r"(?<!\d)8(?!\d)", r"(?<!\d)3(?!\d)"):
        assert re.search(parameter, cells), parameter


def test_no_password_and_no_session_token_is_on_disk(tmp_path: Path):
    """auth-rbac §3: the files hold a salted hash and the hash of the token, also in the WAL file."""
    store = open_store(tmp_path)
    user = add(store)
    token = store.open_session(user.id)
    assert len(token) >= 43, "32 random bytes"
    blob = b"".join(p.read_bytes() for p in auth_files(tmp_path))
    assert blob, "something was written"
    for secret in (PASSWORD, token):
        assert secret.encode() not in blob
    digest = hashlib.sha256(token.encode())
    assert digest.hexdigest().encode() in blob or digest.digest() in blob, "the token is kept as its sha256"


@pytest.mark.parametrize("password", ["", "short12", "x" * 129])
def test_a_password_outside_the_length_limits_is_refused(store, password: str):
    """auth-rbac §3: 8 to 128 characters, for a new account and for a change."""
    error = store_module().AuthError
    with pytest.raises((error, ValueError)):
        add(store, password=password)
    assert store.list_users() == [] or len(store.list_users()) == 0
    user = add(store)
    with pytest.raises((error, ValueError)):
        store.set_password(user.id, password, must_change=False)
    assert store.authenticate("quality", PASSWORD, "10.0.0.1").id == user.id, "the old password still works"


def test_passwords_at_the_length_limits_are_accepted(store, fast: None):
    for number, password in enumerate(("x" * 8, "y" * 128, "비밀번호여덟글자")):
        add(store, f"user{number}", password=password)
        assert store.authenticate(f"user{number}", password, "10.0.0.1").username == f"user{number}"


# ── accounts ────────────────────────────────────────────────────────────────
def test_an_account_round_trips(store):
    """The record carries what the account list shows, and nothing secret."""
    user = add(store, must_change=True)
    assert (user.username, user.role, user.department) == ("quality", "member", "quality")
    assert [u.username for u in store.list_users()] == ["quality"]
    again = store.get_user(user.id)
    assert (again.id, again.display_name, again.disabled) == (user.id, "Tester", False)
    assert again.must_change_password is True
    assert PASSWORD not in repr(again) and "scrypt" not in repr(again)


@pytest.mark.parametrize(
    "username", ["", "ab", "Quality", "_quality", "qual ity", "x" * 33, "qua/lity", "품질팀장"]
)
def test_a_user_name_outside_the_pattern_is_refused(store, username: str):
    """auth-rbac §3: ``[a-z0-9][a-z0-9_-]{2,31}``."""
    with pytest.raises((store_module().AuthError, ValueError)):
        add(store, username)
    assert len(store.list_users()) == 0


@pytest.mark.parametrize("username", ["abc", "a" * 32, "0-9_x"])
def test_a_user_name_inside_the_pattern_is_accepted(store, fast: None, username: str):
    assert add(store, username).username == username


@pytest.mark.parametrize(
    "change",
    [{"department": "auditors"}, {"department": None}, {"role": "owner"}],
    ids=["unknown-department", "member-without-department", "unknown-role"],
)
def test_an_account_without_a_valid_scope_is_never_created(store, change: dict):
    """auth-rbac §1.2, fail closed: refused when it is written, not only when it is used."""
    with pytest.raises((store_module().AuthError, ValueError)):
        add(store, **change)
    assert len(store.list_users()) == 0


def test_a_user_name_is_taken_once(store, fast: None):
    add(store)
    with pytest.raises(store_module().AuthError):
        add(store, display_name="Another", department="legal")
    (only,) = store.list_users()
    assert (only.display_name, only.department) == ("Tester", "quality")


def test_the_store_files_belong_to_their_owner_only(tmp_path: Path):
    """auth-rbac §3: 0600, also for the WAL files."""
    store = open_store(tmp_path)
    store.open_session(add(store).id)
    files = [p for p in auth_files(tmp_path) if p.name.startswith("auth.sqlite")]
    assert files
    for path in files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name


def test_accounts_survive_a_restart(tmp_path: Path, clock: Clock):
    first = open_store(tmp_path, now=clock)
    user = add(first)
    token = first.open_session(user.id)
    second = open_store(tmp_path, now=clock)
    assert second.authenticate("quality", PASSWORD, "10.0.0.1").id == user.id
    assert second.viewer_for(token).user_id == user.id


def test_the_secret_for_opaque_ids_is_random_and_kept(tmp_path: Path):
    """auth-rbac §4: the HMAC key is stable across restarts and different per installation."""
    store = open_store(tmp_path / "a")
    key = store.secret()
    assert isinstance(key, bytes) and len(key) >= 32
    assert open_store(tmp_path / "a").secret() == key
    assert open_store(tmp_path / "b").secret() != key


# ── login ───────────────────────────────────────────────────────────────────
def test_an_unknown_user_gets_the_same_error_and_the_same_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """auth-rbac §3: neither the message nor the time says whether an account exists."""
    calls: list[tuple] = []

    def spy(password, **options):  # type: ignore[no-untyped-def]
        calls.append((options["n"], options["r"], options["p"]))
        return REAL_SCRYPT(password, **options)

    patch_scrypt(monkeypatch, spy)
    store = open_store(tmp_path)
    add(store)
    disabled = add(store, "legal", department="legal")
    store.update_user(disabled.id, role="member", department="legal", disabled=True, display_name="x")
    seen = []
    for username in ("quality", "nobody", "legal", "NOT A NAME", "x" * 64):
        calls.clear()
        error = refused(store, username, "wrong password", f"10.0.0.{len(seen)}")
        seen.append((error.status, error.code, error.message, tuple(calls)))
    assert len(set(seen)) == 1, seen
    status, code, message, work = seen[0]
    assert status == 401 and code and message and work == ((2**15, 8, 3),)
    assert "nobody" not in message and "quality" not in message


def test_a_disabled_account_cannot_log_in_with_its_password(store, fast: None):
    user = add(store)
    store.update_user(user.id, role="member", department="quality", disabled=True, display_name="x")
    wrong = refused(store, "quality", "wrong password", "10.0.0.1")
    right = refused(store, "quality", PASSWORD, "10.0.0.2")
    assert (right.status, right.code, right.message) == (wrong.status, wrong.code, wrong.message)


# ── lockout ─────────────────────────────────────────────────────────────────
def test_five_failures_lock_the_account_for_that_address(store, clock: Clock, fast: None):
    """auth-rbac §3: (user name, IP) 5 times, 5 minutes; another IP is not locked out."""
    add(store)
    for _ in range(4):
        refused(store, "quality", "wrong", "10.0.0.1")
    assert store.authenticate("quality", PASSWORD, "10.0.0.1"), "four failures do not lock"
    clock.now += HOUR
    for _ in range(5):
        refused(store, "quality", "wrong", "10.0.0.1")
    refused(store, "quality", PASSWORD, "10.0.0.1")
    assert store.authenticate("quality", PASSWORD, "10.0.0.2"), "the real user, elsewhere, still gets in"


def test_a_lock_ends_after_five_minutes(store, clock: Clock, fast: None):
    add(store)
    for _ in range(5):
        refused(store, "quality", "wrong", "10.0.0.1")
    clock.now += 290
    refused(store, "quality", PASSWORD, "10.0.0.1")
    clock.now += 301
    assert store.authenticate("quality", PASSWORD, "10.0.0.1")


def test_a_lock_is_the_same_for_an_account_that_does_not_exist(store, fast: None):
    """Security: otherwise the lockout itself would say which accounts exist."""
    add(store)

    def tries(username: str, client: str) -> list[tuple]:
        errors = [refused(store, username, "wrong", client) for _ in range(7)]
        return [(e.status, e.code, e.message) for e in errors]

    assert tries("quality", "10.0.0.1") == tries("nobody", "10.0.0.2")


def test_thirty_failures_lock_the_address_for_every_account(store, fast: None):
    """auth-rbac §3: guessing one password for many accounts is stopped per IP; other IPs go on."""
    add(store)
    for number in range(30):
        refused(store, f"user{number}", "wrong", "10.0.0.1")
    refused(store, "quality", PASSWORD, "10.0.0.1")
    assert store.authenticate("quality", PASSWORD, "10.0.0.2")


def test_twenty_failures_for_other_accounts_do_not_lock_the_address(store, fast: None):
    add(store)
    for number in range(20):
        refused(store, f"user{number}", "wrong", "10.0.0.1")
    assert store.authenticate("quality", PASSWORD, "10.0.0.1")


def _containers(root: object) -> list[Collection]:
    """Every container reachable from the store's own objects."""
    found: list[Collection] = []
    seen: set[int] = set()
    todo: list[object] = [root]
    while todo:
        item = todo.pop()
        if id(item) in seen or isinstance(item, (str, bytes, int, float)):
            continue
        seen.add(id(item))
        if isinstance(item, Mapping):
            found.append(item)
            todo.extend(list(item.values())[:50])
        elif isinstance(item, (list, tuple, set, frozenset)) or type(item).__name__ == "deque":
            found.append(item)  # type: ignore[arg-type]
            todo.extend(list(item)[:50])  # type: ignore[call-overload]
        elif type(item).__module__.startswith("autorfx.auth"):
            slots = [getattr(item, s, None) for s in getattr(type(item), "__slots__", ())]
            todo.extend([*getattr(item, "__dict__", {}).values(), *slots])
    return found


def test_the_memory_of_failures_is_bounded(store, fast: None):
    """auth-rbac §3: an attacker with many names and addresses cannot grow the server's memory."""
    tries = 12_000
    refused(store, "first", "wrong", "10.9.9.9")
    before = max(len(c) for c in _containers(store))
    for number in range(tries):
        refused(store, f"u{number}", "wrong", f"10.{number >> 16 & 255}.{number >> 8 & 255}.{number & 255}")
    sizes = [len(c) for c in _containers(store)]
    assert max(sizes) > before, "the memory that is measured is the one that remembers failures"
    assert max(sizes) <= 10_000, "fewer keys than attempts"


# ── sessions ────────────────────────────────────────────────────────────────
def test_a_session_lasts_twelve_hours_and_not_longer(store, clock: Clock, fast: None):
    """auth-rbac §3: an absolute lifetime; using the session does not extend it."""
    token = store.open_session(add(store).id)
    clock.now += 12 * HOUR - 60
    assert store.viewer_for(token).username == "quality"
    clock.now += 61
    assert store.viewer_for(token) is None
    clock.now -= 2 * HOUR
    assert store.viewer_for("not a token") is None and store.viewer_for("") is None


def test_every_session_has_its_own_token(store, fast: None):
    user = add(store)
    tokens = {store.open_session(user.id) for _ in range(5)}
    assert len(tokens) == 5
    first, *rest = tokens
    store.close_session(first)
    assert store.viewer_for(first) is None and all(store.viewer_for(t) for t in rest)


def test_the_viewer_is_what_the_account_row_says_now(store, fast: None):
    """auth-rbac §3: the row is read on every request, so a change works at once."""
    user = add(store)
    token = store.open_session(user.id)
    store.update_user(user.id, role="member", department="legal", disabled=False, display_name="New")
    viewer = store.viewer_for(token)
    assert (viewer.department, viewer.role, viewer.display_name) == ("legal", "member", "New")
    store.update_user(user.id, role="admin", department=None, disabled=False, display_name="New")
    assert store.viewer_for(token).role == "admin"
    add_admin(store, "other")
    store.update_user(user.id, role="admin", department=None, disabled=True, display_name="New")
    assert store.viewer_for(token) is None


def test_a_new_password_ends_every_session(store, fast: None):
    """auth-rbac §3."""
    user = add(store)
    tokens = [store.open_session(user.id) for _ in range(2)]
    store.set_password(user.id, "another password", must_change=True)
    assert [store.viewer_for(t) for t in tokens] == [None, None]
    refused(store, "quality", PASSWORD, "10.0.0.1")
    assert store.authenticate("quality", "another password", "10.0.0.1").must_change_password is True


def test_sessions_of_one_account_can_be_revoked(store, fast: None):
    user, other = add(store), add(store, "legal", department="legal")
    mine, theirs = store.open_session(user.id), store.open_session(other.id)
    store.revoke_sessions(user.id)
    assert store.viewer_for(mine) is None and store.viewer_for(theirs) is not None


def test_a_random_token_is_nobody(store, fast: None):
    add(store)
    assert store.viewer_for(secrets.token_urlsafe(32)) is None


def test_an_account_in_a_department_that_left_the_rules_gets_no_scope(tmp_path: Path, fast: None, ruleset):
    """RBAC-03, fail closed: after a rule change the stored department is unknown, so nothing is shown."""
    policy = import_module("autorfx.auth.policy")
    token = (first := open_store(tmp_path)).open_session(add(first).id)
    rest = tuple(d for d in DEPARTMENTS if d != "quality")
    viewer = store_module().AuthStore(tmp_path, department_ids=rest).viewer_for(token)
    if viewer is not None:
        fewer = ruleset.model_copy(
            update={"departments": tuple(d for d in ruleset.departments if d.id != "quality")}
        )
        with pytest.raises(policy.PolicyError):
            policy.scope_of(viewer, fewer)


# ── the last administrator ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    "change",
    [
        {"role": "member", "department": "quality", "disabled": False},
        {"role": "admin", "department": None, "disabled": True},
    ],
    ids=["demote", "disable"],
)
def test_the_last_active_administrator_stays(store, fast: None, change: dict):
    """auth-rbac §3: a disabled administrator does not count as one."""
    last = add_admin(store)
    spare = add_admin(store, "spare")
    store.update_user(spare.id, role="admin", department=None, disabled=True, display_name="x")
    with pytest.raises(store_module().AuthError) as caught:
        store.update_user(last.id, display_name="x", **change)
    assert 400 <= caught.value.status < 500
    kept = store.get_user(last.id)
    assert (kept.role, kept.disabled, kept.display_name) == ("admin", False, "Tester"), "nothing changed"
    store.update_user(spare.id, role="admin", department=None, disabled=False, display_name="x")
    store.update_user(last.id, display_name="x", **change)
    assert (store.get_user(last.id).role, store.get_user(last.id).disabled) != ("admin", False)


@pytest.mark.parametrize("change", ["demote", "disable"])
def test_two_administrators_cannot_remove_each_other_at_once(tmp_path: Path, fast: None, change: str):
    """auth-rbac §3: the check is inside the write transaction, so a race leaves one administrator."""
    for attempt in range(8):
        folder = tmp_path / f"{change}{attempt}"
        one, two = open_store(folder), open_store(folder)
        ids = [add_admin(one, "first").id, add_admin(one, "second").id]
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def remove(store, user_id: str, barrier=barrier, outcomes=outcomes) -> None:  # type: ignore[no-untyped-def]
            fields = (
                {"role": "member", "department": "quality", "disabled": False}
                if change == "demote"
                else {"role": "admin", "department": None, "disabled": True}
            )
            barrier.wait()
            try:
                store.update_user(user_id, display_name="x", **fields)
                outcomes.append("done")
            except store_module().AuthError:
                outcomes.append("refused")

        threads = [
            threading.Thread(target=remove, args=(store, user_id))
            for store, user_id in ((one, ids[0]), (two, ids[1]))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert sorted(outcomes) == ["done", "refused"]
        left = [u for u in open_store(folder).list_users() if u.role == "admin" and not u.disabled]
        assert len(left) == 1


def test_one_account_name_is_created_once_by_two_writers(tmp_path: Path, fast: None):
    """Two processes creating the same name: one wins, the other is told so."""
    for attempt in range(5):
        folder = tmp_path / str(attempt)
        stores = [open_store(folder), open_store(folder)]
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def create(store, barrier=barrier, outcomes=outcomes) -> None:  # type: ignore[no-untyped-def]
            barrier.wait()
            try:
                add(store)
                outcomes.append("done")
            except store_module().AuthError:
                outcomes.append("refused")

        threads = [threading.Thread(target=create, args=(store,)) for store in stores]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert sorted(outcomes) == ["done", "refused"] and len(stores[0].list_users()) == 1


# ── a broken store ──────────────────────────────────────────────────────────
def test_a_corrupt_store_never_authenticates(tmp_path: Path):
    """auth-rbac §1.2: a broken store refuses; it is not replaced by an empty one that could be seeded."""
    folder = tmp_path / "work"
    add(open_store(folder))
    for path in auth_files(folder):
        if path.name.startswith("auth.sqlite"):
            path.write_bytes(b"not a database" * 400)
    broken = (folder / "_auth" / "auth.sqlite").read_bytes()
    with pytest.raises(Exception) as caught:  # noqa: B017 - opening may fail, or the first use
        open_store(folder).authenticate("quality", PASSWORD, "10.0.0.1")
    assert getattr(caught.value, "status", 500) in (500, 503)
    assert (folder / "_auth" / "auth.sqlite").read_bytes() == broken, "the evidence is left as it was"
