"""Test accounts and the memo with their passwords (docs/auth-rbac.md §7).

The memo is written while the accounts are still uncommitted: when it cannot be
written, nothing is created, so there is never an account whose password nobody
can read. The memo is git-ignored, readable by its owner only and only added to.
"""

from __future__ import annotations

import os
import secrets
import string
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, TypeVar

from autorfx.auth.policy import Viewer, scope_of
from autorfx.auth.store import AuthStore, NewAccount, UserRecord
from autorfx.guard import assert_not_held_out
from autorfx.rules import Ruleset

T = TypeVar("T")

ADMIN = "admin"
PASSWORD_ALPHABET = string.ascii_letters + string.digits
PASSWORD_LENGTH = 16
MEMO_NAME = "ACCOUNTS.local.md"
WEB_URL = "http://localhost:3000"
HEADER = "| 아이디 | 비밀번호 | 이름 | 역할 | 부서 | 금액 열람 |\n|---|---|---|---|---|---|\n"
NOT_SAVED = "\n위 표의 비밀번호는 저장되지 않았습니다. 쓰지 마세요.\n"


class SeedError(RuntimeError):
    """The memo cannot be written, so nothing was changed. The message is shown to the user."""


def memo_path(start: Path) -> Path:
    """The memo at the root of the repository that holds ``start`` (next to ``pyproject.toml``)."""
    folder = start.resolve()
    root = next((f for f in (folder, *folder.parents) if (f / "pyproject.toml").is_file()), folder)
    return root / MEMO_NAME


def new_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def seed_accounts(
    store: AuthStore, ruleset: Ruleset, memo_path: Path, *, web_url: str = WEB_URL
) -> tuple[UserRecord, ...]:
    """Create the accounts that are missing: an administrator and one member per rule department."""
    accounts = [NewAccount(ADMIN, new_password(), "관리자", "admin", None, False)]
    accounts += [
        NewAccount(d.id, new_password(), d.team_label, "member", d.id, False) for d in ruleset.departments
    ]
    return _with_memo(
        memo_path,
        lambda write: store.create_missing(
            accounts, before_commit=lambda created: write(_section("계정", created, ruleset, web_url))
        ),
    )


def reset_password(
    store: AuthStore, ruleset: Ruleset, memo_path: Path, username: str, *, web_url: str = WEB_URL
) -> UserRecord:
    """A new random password for one account, written to the memo; its sessions end."""
    user = store.find_username(username)
    if user is None:
        raise SeedError(f"'{username}': 없는 계정입니다. `autorfx users list`로 확인하세요.")
    password = new_password()
    renewed = NewAccount(user.username, password, user.display_name, user.role, user.department, False)
    text = _section("새 비밀번호", [renewed], ruleset, web_url)
    return _with_memo(
        memo_path,
        lambda write: store.set_password(
            user.id, password, must_change=False, before_commit=lambda: write(text)
        ),
    )


def _with_memo(path: Path, change: Callable[[Callable[[str], None]], T]) -> T:
    """Run ``change``, which writes the memo just before it commits. Text of a change that was not
    committed after all is marked in the memo as not valid."""
    assert_not_held_out(path)
    written = False

    def write(text: str) -> None:
        nonlocal written
        _append(path, text)
        written = True

    try:
        return change(write)
    except BaseException:
        if written:
            _append(path, NOT_SAVED)
        raise


def _append(path: Path, text: str) -> None:
    """Add ``text`` at the end and make sure it is on disk; a write that fails half way is taken back."""
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(path, flags, 0o600), "r+b", buffering=0) as memo:
            path.chmod(0o600)
            size = os.fstat(memo.fileno()).st_size
            try:
                data = _separator(memo, size) + text.encode("utf-8")
                if memo.write(data) != len(data):
                    raise OSError("일부만 기록됐습니다")
                os.fsync(memo.fileno())
            except OSError:
                memo.truncate(size)
                raise
    except OSError as exc:
        raise SeedError(
            f"메모를 쓸 수 없어 계정을 바꾸지 않았습니다: {path} ({exc.strerror or exc})"
        ) from exc


def _separator(memo: BinaryIO, size: int) -> bytes:
    """A blank line between what the file holds and the new section."""
    if size == 0:
        return b""
    memo.seek(size - 1)
    return b"\n" if memo.read(1) == b"\n" else b"\n\n"


def _section(title: str, accounts: Sequence[NewAccount], ruleset: Ruleset, web_url: str) -> str:
    rows = "".join(_row(account, ruleset) for account in accounts)
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        f"## AutoRFx {title} ({when})\n\n"
        f"- 화면 주소: {web_url}\n"
        "- 이 파일은 git에 올리지 않습니다 (저장소가 공개입니다).\n\n"
        f"{HEADER}{rows}"
    )


def _row(account: NewAccount, ruleset: Ruleset) -> str:
    viewer = Viewer("", account.username, account.display_name, account.role, account.department, False)
    cost = "봄" if scope_of(viewer, ruleset).cost_visible else "못 봄"
    rule = ruleset.department(account.department) if account.department else None
    cells = (
        account.username,
        f"`{account.password}`",
        _cell(account.display_name),
        account.role,
        _cell(rule.label) if rule else "-",
        cost,
    )
    return f"| {' | '.join(cells)} |\n"


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "/").split())
