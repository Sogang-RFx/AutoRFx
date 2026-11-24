"""Who may see and do what (docs/auth-rbac.md §2).

The only inputs are the account behind the session and ``departments.yaml``.
Anything not known — a role, a department, a member without one — is refused
with :class:`PolicyError`, never given a default scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from autorfx.models import Group
from autorfx.rules import DepartmentRule, Ruleset

Role = Literal["admin", "member"]
ROLES: tuple[Role, ...] = get_args(Role)


class PolicyError(PermissionError):
    """The account has no valid scope. The message is shown to the user."""


@dataclass(frozen=True)
class Viewer:
    """The account behind a session, as it is at this request."""

    user_id: str
    username: str
    display_name: str
    role: Role
    department: str | None
    must_change_password: bool


@dataclass(frozen=True)
class Scope:
    """What an account sees. ``areas`` are rule departments; ``None`` is every clause.

    There are no defaults: a scope that was not decided cannot be built by accident.
    """

    cost_visible: bool
    areas: frozenset[str] | None

    @property
    def is_full(self) -> bool:
        return self.cost_visible and self.areas is None

    @property
    def group(self) -> Group:
        return "cost_visible" if self.cost_visible else "cost_restricted"


def _department(viewer: Viewer, ruleset: Ruleset) -> DepartmentRule:
    if viewer.department is None:
        raise PolicyError("부서가 없는 계정입니다. 관리자에게 부서 지정을 요청하세요.")
    rule = ruleset.department(viewer.department)
    if rule is None:
        raise PolicyError("계정의 부서가 부서 목록에 없습니다. 관리자에게 확인을 요청하세요.")
    return rule


def scope_of(viewer: Viewer, ruleset: Ruleset) -> Scope:
    if viewer.role == "admin":
        return Scope(cost_visible=True, areas=None)
    if viewer.role != "member":
        raise PolicyError("알 수 없는 역할의 계정입니다. 관리자에게 확인을 요청하세요.")
    rule = _department(viewer, ruleset)
    return Scope(
        cost_visible=rule.cost_visible,
        areas=None if rule.receives_all else frozenset({rule.id}),
    )


def can_upload(viewer: Viewer, ruleset: Ruleset) -> bool:
    """Administrators and the departments that receive every RFQ."""
    try:
        return scope_of(viewer, ruleset).areas is None
    except PolicyError:
        return False  # an account without a scope may do nothing


def can_manage(viewer: Viewer) -> bool:
    return viewer.role == "admin"
