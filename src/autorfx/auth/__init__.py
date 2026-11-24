"""Accounts, sessions and what an account may see (docs/auth-rbac.md).

Identity comes from a session only. Who may see what is decided in
:mod:`autorfx.auth.policy` from the account and the department rules; the
accounts themselves are in :mod:`autorfx.auth.store`.
"""

from __future__ import annotations

from autorfx.auth.audit import AuditLog
from autorfx.auth.policy import PolicyError, Role, Scope, Viewer, can_manage, can_upload, scope_of
from autorfx.auth.store import AuthError, AuthStore, LockedError, UserRecord

__all__ = [
    "AuditLog",
    "AuthError",
    "AuthStore",
    "LockedError",
    "PolicyError",
    "Role",
    "Scope",
    "UserRecord",
    "Viewer",
    "can_manage",
    "can_upload",
    "scope_of",
]
