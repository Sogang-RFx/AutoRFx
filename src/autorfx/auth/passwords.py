"""Password hashing: scrypt from the standard library.

The parameters are stored with each hash, so they can be raised later without
breaking the accounts that exist. One hash takes 32 MB; how many run at the
same time is bounded here, whoever calls, so that a login flood cannot use up
the memory of the server.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading

N, R, P = 2**15, 8, 3
MAXMEM = 64 * 1024 * 1024
KEY_BYTES = 32
SALT_BYTES = 16
MIN_LENGTH, MAX_LENGTH = 8, 128
MAX_CONCURRENT = 2
SCHEME = "scrypt"

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


class PasswordPolicyError(ValueError):
    """A password that may not be used. The message is shown to the user."""


class PasswordHashError(ValueError):
    """A stored hash that cannot be read: the account store is damaged."""


def check_policy(password: str) -> None:
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        raise PasswordPolicyError(f"비밀번호는 {MIN_LENGTH}자 이상 {MAX_LENGTH}자 이하로 정하세요.")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    with _slots:
        return hashlib.scrypt(
            password.encode("utf-8", "surrogatepass"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=MAXMEM,
            dklen=KEY_BYTES,
        )


def _format(salt: bytes, key: bytes) -> str:
    parts = (base64.b64encode(value).decode("ascii") for value in (salt, key))
    return "$".join((SCHEME, str(N), str(R), str(P), *parts))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    return _format(salt, _derive(password, salt, N, R, P))


def verify_password(password: str, stored: str) -> bool:
    """Whether ``password`` is the one behind ``stored``. A hash that cannot be read is an error."""
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != SCHEME:
            raise ValueError(f"알 수 없는 방식 {scheme!r}")
        expected = base64.b64decode(key, validate=True)
        if len(expected) != KEY_BYTES:
            raise ValueError("길이가 맞지 않습니다")
        found = _derive(password, base64.b64decode(salt, validate=True), int(n), int(r), int(p))
    except ValueError as exc:
        raise PasswordHashError("저장된 비밀번호 해시를 읽을 수 없습니다.") from exc
    return hmac.compare_digest(found, expected)


DUMMY_HASH = _format(secrets.token_bytes(SALT_BYTES), secrets.token_bytes(KEY_BYTES))
"""Checked when the account does not exist, so that it costs as much as a wrong password.
Nobody knows a password for it: the key is random, not derived from one."""
