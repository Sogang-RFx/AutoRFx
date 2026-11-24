"""Accounts and sessions: ``work/_auth/auth.sqlite`` (docs/auth-rbac.md §3).

Every call opens its own short connection, so the store can be used from any
thread and by the command line next to a running server. Writes take the write
lock first (``BEGIN IMMEDIATE``): a check and the change that depends on it are
one step. A store that cannot be read raises :class:`AuthError` (503) on every
call; nothing falls back to "no accounts" or to an older copy.

A session token is kept only as its SHA-256. The account row is read again for
every request, so disabling an account or moving it to another department takes
effect at once.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from autorfx.auth import passwords
from autorfx.auth.policy import ROLES, Role, Viewer
from autorfx.auth.throttle import LoginThrottle, TooManyAttempts

log = logging.getLogger(__name__)

AUTH_DIR = "_auth"
DB_NAME = "auth.sqlite"
SCHEMA_VERSION = 1
BUSY_SECONDS = 30.0
SESSION_HOURS = 12.0
MAX_TOKEN = 128
MAX_DISPLAY_NAME = 60
USERNAME = re.compile(r"[a-z0-9][a-z0-9_-]{2,31}")
SECRET_KEY = "opaque_id_secret"

SCHEMA = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value BLOB NOT NULL)",
    """CREATE TABLE users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
        department TEXT,
        password_hash TEXT NOT NULL,
        epoch INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        must_change_password INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        last_login INTEGER,
        CHECK ((role = 'admin') = (department IS NULL))
    )""",
    """CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
    )""",
    "CREATE INDEX sessions_by_user ON sessions (user_id)",
)

BAD_LOGIN = "아이디 또는 비밀번호가 맞지 않습니다."
LOCKED = "로그인 시도가 너무 많습니다. 5분쯤 뒤에 다시 시도하세요."
UNAVAILABLE = "계정 저장소를 읽을 수 없습니다. 관리자에게 문의하세요."


class AuthError(Exception):
    """An error with an HTTP status and a message for the user."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class LockedError(AuthError):
    """Too many attempts. ``first`` is true for the first refusal of a lock."""

    def __init__(self, *, first: bool) -> None:
        super().__init__(429, "too_many_attempts", LOCKED)
        self.first = first


class _Unset:
    """A field that was not sent: it keeps its value (``None`` is a value of ``department``)."""


UNSET: Final = _Unset()


@dataclass(frozen=True)
class UserRecord:
    """An account as it may be shown; the password hash is not part of it. Times are in milliseconds."""

    id: str
    username: str
    display_name: str
    role: Role
    department: str | None
    disabled: bool
    must_change_password: bool
    created_at: int
    last_login: int | None
    sessions: int
    epoch: int
    """Grows with every password change: what was checked with an older password is out of date."""

    @property
    def viewer(self) -> Viewer:
        return Viewer(
            user_id=self.id,
            username=self.username,
            display_name=self.display_name,
            role=self.role,
            department=self.department,
            must_change_password=self.must_change_password,
        )


@dataclass(frozen=True)
class NewAccount:
    username: str
    password: str
    display_name: str
    role: Role
    department: str | None
    must_change: bool


def private_folder(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


def _invalid(message: str) -> AuthError:
    return AuthError(422, "invalid_request", message)


def _bad_login() -> AuthError:
    return AuthError(401, "invalid_credentials", BAD_LOGIN)


def _unavailable() -> AuthError:
    return AuthError(503, "auth_unavailable", UNAVAILABLE)


class AuthStore:
    def __init__(
        self,
        work_dir: Path,
        *,
        department_ids: Sequence[str],
        now: Callable[[], float] = time.time,
        session_hours: float = SESSION_HOURS,
    ) -> None:
        self.path = Path(work_dir).resolve() / AUTH_DIR / DB_NAME
        self._departments = frozenset(department_ids)
        self._now = now
        self._session_ms = int(session_hours * 3600 * 1000)
        self._throttle = LoginThrottle(now)
        self._open_lock = threading.Lock()
        self._is_open = False
        try:
            self._ensure_open()
        except AuthError:
            # Logged with its cause. The process starts, and every call below raises the same error.
            log.error("the account store cannot be opened: every login and every account is refused")

    # ── connections ─────────────────────────────────────────────────────────
    def _ms(self) -> int:
        return int(self._now() * 1000)

    def _ensure_open(self) -> None:
        with self._open_lock:
            if self._is_open:
                return
            with _store_errors():
                self._open()
            self._is_open = True

    def _open(self) -> None:
        private_folder(self.path.parent)
        # Created here and not by sqlite, so that it is never readable by others; the -wal and -shm
        # files take the mode of this one.
        os.close(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600))
        for suffix in ("", "-wal", "-shm"):
            file = self.path.with_name(self.path.name + suffix)
            if file.exists():
                file.chmod(0o600)
        with closing(self._connect()) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("quick_check failed")
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                db.execute("PRAGMA journal_mode = WAL")
            with _transaction(db):
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version == SCHEMA_VERSION:
                    return
                if version != 0 or db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
                    raise sqlite3.DatabaseError(f"unknown schema version {version}")
                for statement in SCHEMA:
                    db.execute(statement)
                db.execute("INSERT INTO meta VALUES (?, ?)", (SECRET_KEY, secrets.token_bytes(32)))
                db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _connect(self) -> sqlite3.Connection:
        # mode=rw: a store that disappeared while the server runs is an error, not a new empty store.
        db = sqlite3.connect(
            f"{self.path.as_uri()}?mode=rw", uri=True, timeout=BUSY_SECONDS, isolation_level=None
        )
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA synchronous = FULL")
        except BaseException:
            db.close()
            raise
        return db

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        self._ensure_open()
        with _store_errors(), closing(self._connect()) as db:
            yield db

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._read() as db, _transaction(db):
            yield db

    # ── accounts ────────────────────────────────────────────────────────────
    def create_user(
        self,
        username: str,
        password: str,
        *,
        display_name: str,
        role: Role,
        department: str | None,
        must_change: bool,
    ) -> UserRecord:
        account = NewAccount(username, password, display_name, role, department, must_change)
        self._check_new(account)
        hashed = passwords.hash_password(password)
        with self._write() as db:
            return self._insert(db, account, hashed)

    def create_missing(
        self, accounts: Iterable[NewAccount], *, before_commit: Callable[[Sequence[NewAccount]], None]
    ) -> tuple[UserRecord, ...]:
        """Create the accounts whose username is not taken, all or none of them.

        ``before_commit`` gets the ones being created while the write lock is held. When it fails,
        nothing is created; a second call at the same time waits and finds the accounts there.
        """
        wanted = tuple(accounts)
        for account in wanted:
            self._check_new(account)
        taken = {u.username for u in self.list_users()}
        # Hashed before the lock is taken: it is slow on purpose.
        hashes = {a.username: passwords.hash_password(a.password) for a in wanted if a.username not in taken}
        with self._write() as db:
            missing = [a for a in wanted if a.username in hashes and _by_name(db, a.username) is None]
            created = tuple(self._insert(db, account, hashes[account.username]) for account in missing)
            if missing:
                before_commit(missing)
            return created

    def list_users(self) -> tuple[UserRecord, ...]:
        with self._read() as db:
            rows = db.execute("SELECT * FROM users ORDER BY created_at, username").fetchall()
            return tuple(self._record(db, row) for row in rows)

    def get_user(self, user_id: str) -> UserRecord:
        with self._read() as db:
            return self._record(db, _row(db, user_id))

    def find_username(self, username: str) -> UserRecord | None:
        with self._read() as db:
            row = _by_name(db, username)
            return self._record(db, row) if row is not None else None

    def update_user(
        self,
        user_id: str,
        *,
        role: Role | _Unset = UNSET,
        department: str | None | _Unset = UNSET,
        disabled: bool | _Unset = UNSET,
        display_name: str | _Unset = UNSET,
    ) -> UserRecord:
        """Change what is given. The last active administrator stays one."""
        with self._write() as db:
            row = _row(db, user_id)
            new_role = row["role"] if isinstance(role, _Unset) else role
            new_department = row["department"] if isinstance(department, _Unset) else department
            new_disabled = bool(row["disabled"]) if isinstance(disabled, _Unset) else disabled
            new_name = row["display_name"] if isinstance(display_name, _Unset) else display_name.strip()
            if not isinstance(display_name, _Unset):
                _check_display_name(new_name)
            if not (isinstance(role, _Unset) and isinstance(department, _Unset)):
                self._check_role(new_role, new_department)
            db.execute(
                "UPDATE users SET role = ?, department = ?, disabled = ?, display_name = ? WHERE id = ?",
                (new_role, new_department, int(new_disabled), new_name, user_id),
            )
            if new_disabled:
                db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            _keep_an_admin(db, row)
            return self._record(db, _row(db, user_id))

    def set_password(
        self,
        user_id: str,
        password: str,
        *,
        must_change: bool,
        expected_epoch: int | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> UserRecord:
        """Set the password and end every session of the account.

        With ``expected_epoch`` the change is refused when the password was changed meanwhile.
        """
        _check_password(password)
        hashed = passwords.hash_password(password)
        with self._write() as db:
            row = _row(db, user_id)
            if expected_epoch is not None and row["epoch"] != expected_epoch:
                raise _bad_login()
            db.execute(
                "UPDATE users SET password_hash = ?, must_change_password = ?, epoch = epoch + 1"
                " WHERE id = ?",
                (hashed, int(must_change), user_id),
            )
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            if before_commit is not None:
                before_commit()
            return self._record(db, _row(db, user_id))

    def delete_user(self, user_id: str) -> UserRecord:
        with self._write() as db:
            row = _row(db, user_id)
            record = self._record(db, row)
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            _keep_an_admin(db, row)
            return record

    # ── login and sessions ──────────────────────────────────────────────────
    def authenticate(self, username: str, password: str, client: str) -> UserRecord:
        """The account of a correct pair. An unknown account, a wrong password and a disabled account
        get the same answer and cost the same."""
        try:
            attempt = self._throttle.begin(username, client)
        except TooManyAttempts as exc:
            raise LockedError(first=exc.first) from exc
        try:
            with self._read() as db:
                row = _by_name(db, username)
                record = self._record(db, row) if row is not None else None
        except AuthError:
            self._throttle.cancel(attempt)
            raise
        stored = row["password_hash"] if row is not None else passwords.DUMMY_HASH
        try:
            matches = passwords.verify_password(password, stored)
        except passwords.PasswordHashError as exc:
            log.exception("the password hash of an account cannot be read")
            raise _unavailable() from exc
        if not matches or record is None or record.disabled:
            raise _bad_login()
        self._throttle.succeed(attempt)
        return record

    def open_session(self, user_id: str, *, expected_epoch: int | None = None) -> str:
        """A new session token. Refused when the account was disabled, deleted, or its password changed
        (``expected_epoch``) since it was checked."""
        token = secrets.token_urlsafe(32)
        now = self._ms()
        with self._write() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None or row["disabled"]:
                raise _bad_login()
            if expected_epoch is not None and row["epoch"] != expected_epoch:
                raise _bad_login()
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                (_hash_token(token), user_id, now, now + self._session_ms),
            )
            db.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user_id))
        return token

    def viewer_for(self, token: str) -> Viewer | None:
        if not token or len(token) > MAX_TOKEN:
            return None
        with self._read() as db:
            row = db.execute(
                "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id"
                " WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.disabled = 0",
                (_hash_token(token), self._ms()),
            ).fetchone()
            return self._record(db, row).viewer if row is not None else None

    def close_session(self, token: str) -> None:
        with self._write() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))

    def revoke_sessions(self, user_id: str) -> int:
        """End every session of the account; returns how many there were."""
        with self._write() as db:
            _row(db, user_id)
            return db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount

    @property
    def session_seconds(self) -> int:
        return self._session_ms // 1000

    def secret(self) -> bytes:
        """Key of the opaque ids of hidden clauses and issues."""
        with self._read() as db:
            row = db.execute("SELECT value FROM meta WHERE key = ?", (SECRET_KEY,)).fetchone()
        if row is None or len(row["value"]) != 32:
            log.error("the account store has no valid secret")
            raise _unavailable()
        return bytes(row["value"])

    # ── rows ────────────────────────────────────────────────────────────────
    def _check_new(self, account: NewAccount) -> None:
        if not USERNAME.fullmatch(account.username):
            raise _invalid("아이디는 영어 소문자, 숫자, _, - 로 3자 이상 32자 이하로 정하세요.")
        _check_display_name(account.display_name.strip())
        self._check_role(account.role, account.department)
        _check_password(account.password)

    def _check_role(self, role: str, department: str | None) -> None:
        if role not in ROLES:
            raise _invalid("역할은 admin 또는 member입니다.")
        if role == "admin":
            if department is not None:
                raise _invalid("관리자(admin)는 부서를 두지 않습니다.")
        elif department is None:
            raise _invalid("부서 계정(member)은 부서를 골라야 합니다.")
        elif department not in self._departments:
            raise _invalid("없는 부서입니다.")

    def _insert(self, db: sqlite3.Connection, account: NewAccount, hashed: str) -> UserRecord:
        if _by_name(db, account.username) is not None:
            raise AuthError(409, "duplicate_username", "이미 있는 아이디입니다.")
        user_id = secrets.token_hex(12)
        db.execute(
            "INSERT INTO users (id, username, display_name, role, department, password_hash,"
            " must_change_password, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                account.username,
                account.display_name.strip(),
                account.role,
                account.department,
                hashed,
                int(account.must_change),
                self._ms(),
            ),
        )
        return self._record(db, _row(db, user_id))

    def _record(self, db: sqlite3.Connection, row: sqlite3.Row) -> UserRecord:
        count = db.execute(
            "SELECT count(*) FROM sessions WHERE user_id = ? AND expires_at > ?", (row["id"], self._ms())
        ).fetchone()[0]
        return UserRecord(
            id=row["id"],
            username=row["username"],
            display_name=row["display_name"],
            role=row["role"],
            department=row["department"],
            disabled=bool(row["disabled"]),
            must_change_password=bool(row["must_change_password"]),
            created_at=row["created_at"],
            last_login=row["last_login"],
            sessions=count,
            epoch=row["epoch"],
        )


# ── helpers ─────────────────────────────────────────────────────────────────
@contextmanager
def _store_errors() -> Iterator[None]:
    """A database or file problem is one error for the caller; the cause goes to the log."""
    try:
        yield
    except (sqlite3.Error, OSError) as exc:
        log.exception("account store error")
        raise _unavailable() from exc


@contextmanager
def _transaction(db: sqlite3.Connection) -> Iterator[None]:
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def _by_name(db: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def _row(db: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise AuthError(404, "not_found", "계정을 찾을 수 없습니다.")
    return row


def _keep_an_admin(db: sqlite3.Connection, before: sqlite3.Row) -> None:
    """Refuse the change of this transaction when it took away the last active administrator."""
    if before["role"] != "admin" or before["disabled"]:
        return
    if not db.execute("SELECT count(*) FROM users WHERE role = 'admin' AND disabled = 0").fetchone()[0]:
        raise AuthError(409, "last_admin", "마지막 관리자 계정은 삭제하거나 강등하거나 막을 수 없습니다.")


def _check_display_name(name: str) -> None:
    if not 1 <= len(name) <= MAX_DISPLAY_NAME:
        raise _invalid(f"이름은 1자 이상 {MAX_DISPLAY_NAME}자 이하로 정하세요.")


def _check_password(password: str) -> None:
    try:
        passwords.check_policy(password)
    except passwords.PasswordPolicyError as exc:
        raise AuthError(422, "weak_password", str(exc)) from exc
