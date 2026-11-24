"""Seeding the accounts and the memo of their passwords (docs/auth-rbac.md §7)."""

from __future__ import annotations

import re
import shutil
import stat
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path

import pytest

from auth_helpers import ACCOUNTS, DEPARTMENTS, store_module
from autorfx.config import get_settings
from autorfx.rules import Ruleset
from conftest import RULES_DIR

TOKEN = re.compile(r"[^\s|`,:]+")


def open_store(folder: Path):  # type: ignore[no-untyped-def]
    folder.mkdir(parents=True, exist_ok=True)
    return store_module().AuthStore(folder, department_ids=DEPARTMENTS)


def seed(store, ruleset: Ruleset, memo: Path):  # type: ignore[no-untyped-def]
    return import_module("autorfx.auth.seed").seed_accounts(store, ruleset, memo)


def passwords_in(memo: Path) -> dict[str, list[str]]:
    """Per account, the candidate passwords on the lines of the memo that name it."""
    found: dict[str, list[str]] = {}
    for line in memo.read_text(encoding="utf-8").splitlines():
        tokens = TOKEN.findall(line)
        for name in ACCOUNTS:
            if name in tokens:
                others = [t for t in tokens if t not in ACCOUNTS and len(t) >= 12]
                found.setdefault(name, []).extend(others)
    return found


def working(store, memo: Path) -> dict[str, list[str]]:  # type: ignore[no-untyped-def]
    """Per account, the memo's passwords that really log in. Each try comes from its own address."""
    error = store_module().AuthError
    out: dict[str, list[str]] = {}
    for name, candidates in passwords_in(memo).items():
        for number, candidate in enumerate(candidates):
            try:
                store.authenticate(name, candidate, f"10.1.{len(out)}.{number}")
            except error:
                continue
            out.setdefault(name, []).append(candidate)
    return out


def test_one_account_per_department_and_one_administrator(tmp_path: Path, ruleset: Ruleset):
    """auth-rbac §7: the user name is the department id; nobody is asked to change the password."""
    store = open_store(tmp_path / "work")
    seed(store, ruleset, tmp_path / "ACCOUNTS.local.md")
    users = {u.username: u for u in store.list_users()}
    assert set(users) == set(ACCOUNTS) and len(users) == 9
    assert (users["admin"].role, users["admin"].department) == ("admin", None)
    for name in DEPARTMENTS:
        assert (users[name].role, users[name].department) == ("member", name)
    assert not any(u.must_change_password or u.disabled for u in users.values())


def test_the_memo_has_one_password_per_account_that_logs_in(tmp_path: Path, ruleset: Ruleset):
    """auth-rbac §7: random passwords, all different, in a file only its owner can read."""
    store, memo = open_store(tmp_path / "work"), tmp_path / "ACCOUNTS.local.md"
    seed(store, ruleset, memo)
    found = working(store, memo)
    assert set(found) == set(ACCOUNTS) and all(len(v) == 1 for v in found.values()), found.keys()
    assert len({v[0] for v in found.values()}) == 9
    assert stat.S_IMODE(memo.stat().st_mode) == 0o600
    for path in (p for p in (tmp_path / "work" / "_auth").rglob("*") if p.is_file()):
        assert not any(v[0].encode() in path.read_bytes() for v in found.values()), path.name


def test_passwords_differ_between_installations(tmp_path: Path, ruleset: Ruleset):
    memos = []
    for number in range(2):
        memos.append(tmp_path / f"{number}.md")
        seed(open_store(tmp_path / str(number)), ruleset, memos[-1])
    one, two = (passwords_in(m) for m in memos)
    assert not {p for v in one.values() for p in v} & {p for v in two.values() for p in v}


def test_seeding_again_changes_nothing(tmp_path: Path, ruleset: Ruleset):
    """auth-rbac §7: existing accounts are not touched, and no second password is written for them."""
    store, memo = open_store(tmp_path / "work"), tmp_path / "ACCOUNTS.local.md"
    seed(store, ruleset, memo)
    before = memo.read_bytes()
    quality = next(u for u in store.list_users() if u.username == "quality")
    store.set_password(quality.id, "changed by hand 1", must_change=False)
    seed(store, ruleset, memo)
    assert store.authenticate("quality", "changed by hand 1", "10.0.0.1").id == quality.id
    assert memo.read_bytes() == before and len(store.list_users()) == 9


def test_only_the_missing_accounts_are_created_and_appended(tmp_path: Path, ruleset: Ruleset):
    """auth-rbac §7: an existing memo is kept as it is and only new accounts are added to it."""
    store, memo = open_store(tmp_path / "work"), tmp_path / "ACCOUNTS.local.md"
    for name in ("admin", "quality"):
        store.create_user(
            name,
            f"my own password {name}",
            display_name=name,
            role="admin" if name == "admin" else "member",
            department=None if name == "admin" else name,
            must_change=False,
        )
    memo.write_text("my notes stay\n", encoding="utf-8")
    memo.chmod(0o644)
    seed(store, ruleset, memo)
    assert memo.read_text(encoding="utf-8").startswith("my notes stay\n")
    assert set(working(store, memo)) == set(ACCOUNTS) - {"admin", "quality"}
    assert store.authenticate("quality", "my own password quality", "10.0.0.1")
    assert stat.S_IMODE(memo.stat().st_mode) == 0o600, "an older, readable memo is closed"


@pytest.mark.parametrize("how", ["folder-read-only", "a-folder-is-in-the-way"])
def test_without_a_memo_no_account_is_created(tmp_path: Path, ruleset: Ruleset, how: str):
    """auth-rbac §7: an account whose password nobody can read is never created."""
    store = open_store(tmp_path / "work")
    place = tmp_path / "place"
    place.mkdir()
    memo = place / "ACCOUNTS.local.md"
    if how == "folder-read-only":
        place.chmod(0o500)
    else:
        memo.mkdir()
    try:
        with pytest.raises(Exception) as caught:  # noqa: B017 - any refusal, but a refusal
            seed(store, ruleset, memo)
        assert not isinstance(caught.value, (ModuleNotFoundError, AttributeError))
        assert len(store.list_users()) == 0
    finally:
        place.chmod(0o700)


SEED_SCRIPT = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from autorfx.auth.seed import seed_accounts
    from autorfx.auth.store import AuthStore
    from autorfx.rules import load_ruleset

    work, memo, rules, go, ready = map(Path, sys.argv[1:6])
    ruleset = load_ruleset(rules)
    store = AuthStore(work, department_ids=tuple(ruleset.department_ids))
    ready.touch()
    while not go.exists():
        time.sleep(0.005)
    seed_accounts(store, ruleset, memo)
    """
)


def test_two_processes_seeding_at_once_create_each_account_once(tmp_path: Path, ruleset: Ruleset):
    """auth-rbac §7: both end well; every account exists once, and the memo has exactly the password of
    the account that was created, not the one the other process made up."""
    work, memo, go = tmp_path / "work", tmp_path / "ACCOUNTS.local.md", tmp_path / "go"
    work.mkdir()
    script = tmp_path / "seed_once.py"
    script.write_text(SEED_SCRIPT, encoding="utf-8")
    ready = [tmp_path / f"ready{n}" for n in range(2)]
    running = [
        subprocess.Popen(
            [sys.executable, str(script), *map(str, (work, memo, RULES_DIR, go, flag))],
            stderr=subprocess.PIPE,
            text=True,
        )
        for flag in ready
    ]
    try:
        for _ in range(3000):
            if all(f.exists() for f in ready) or any(p.poll() is not None for p in running):
                break
            time.sleep(0.01)
        go.touch()
        errors = [p.communicate(timeout=120)[1] for p in running]
    finally:
        for process in running:
            process.kill()
    assert [p.returncode for p in running] == [0, 0], errors
    store = open_store(work)
    assert sorted(u.username for u in store.list_users()) == sorted(ACCOUNTS)
    found = working(store, memo)
    assert set(found) == set(ACCOUNTS) and all(len(v) == 1 for v in found.values())
    assert {name: len(v) for name, v in passwords_in(memo).items()} == dict.fromkeys(ACCOUNTS, 1)
    assert stat.S_IMODE(memo.stat().st_mode) == 0o600


# ── command line ────────────────────────────────────────────────────────────
@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "data"
    shutil.copytree(RULES_DIR, root / "rules")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTORFX_DATA_DIR", str(root))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def run_cli(*arguments: str) -> int:
    """``main`` as a process would end: an unknown command is an exit code too."""
    from autorfx.cli import main

    try:
        return main(list(arguments))
    except SystemExit as exc:
        return int(exc.code or 0)


def test_users_are_listed_without_secrets(data_dir: Path, capsys: pytest.CaptureFixture[str]):
    """auth-rbac §7: ``users list``."""
    store = open_store(data_dir / "work")
    store.create_user(
        "quality", "my own password", display_name="q", role="member", department="quality", must_change=False
    )
    assert run_cli("users", "list") == 0
    out = capsys.readouterr().out
    assert "quality" in out and "my own password" not in out and "scrypt" not in out


def test_a_reset_gives_a_new_password_and_ends_the_sessions(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
):
    """auth-rbac §7: ``users reset <id>``; the old password stops working."""
    store = open_store(data_dir / "work")
    user = store.create_user(
        "quality", "my own password", display_name="q", role="member", department="quality", must_change=False
    )
    token = store.open_session(user.id)
    assert run_cli("users", "reset", "quality") == 0
    capsys.readouterr()
    assert store.viewer_for(token) is None
    with pytest.raises(store_module().AuthError):
        store.authenticate("quality", "my own password", "10.0.0.1")
    assert run_cli("users", "reset", "nobody") != 0


def test_seeding_from_the_command_line_creates_the_accounts(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
):
    """auth-rbac §7: ``users seed``; the passwords go to the memo, not to the terminal."""
    assert run_cli("users", "seed") == 0
    out = capsys.readouterr()
    store = open_store(data_dir / "work")
    assert len(store.list_users()) == 9
    memos = list(data_dir.parent.rglob("ACCOUNTS.local.md"))
    assert len(memos) == 1, "the memo is written where the command runs"
    found = working(store, memos[0])
    assert set(found) == set(ACCOUNTS)
    assert not any(v[0] in out.out + out.err for v in found.values())
