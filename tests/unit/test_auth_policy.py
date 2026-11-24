"""Roles and scopes (docs/auth-rbac.md §2, §3): from the account and the rule files."""

from __future__ import annotations

import dataclasses

import pytest

from auth_helpers import ACCOUNTS, COST_VISIBLE, DEPARTMENTS, FULL, LIMITED, policy, scope_for, viewer
from autorfx.rules import Ruleset


def test_the_rule_files_name_the_departments_these_tests_use(ruleset: Ruleset):
    """Precondition (green before the change): the table of §2 against data/rules/departments.yaml."""
    assert set(ruleset.department_ids) == set(DEPARTMENTS)
    assert [d.id for d in ruleset.departments if d.receives_all] == ["sales"]
    assert {d for d in DEPARTMENTS if ruleset.group_of(d) == "cost_visible"} == set(COST_VISIBLE) - {"admin"}


@pytest.mark.parametrize("name", FULL)
def test_admin_and_the_receiving_department_have_the_full_scope(ruleset: Ruleset, name: str):
    """RBAC-01: everything, cost included."""
    scope = scope_for(name, ruleset)
    assert (scope.is_full, scope.areas, scope.cost_visible, scope.group) == (True, None, True, "cost_visible")


@pytest.mark.parametrize("name", LIMITED)
def test_a_department_has_its_own_area_only(ruleset: Ruleset, name: str):
    """RBAC-01: one department, and cost clauses only when the rule files say so."""
    scope = scope_for(name, ruleset)
    cost = name in COST_VISIBLE
    assert scope.areas == frozenset({name}) and not scope.is_full
    assert (scope.cost_visible, scope.group) == (cost, "cost_visible" if cost else "cost_restricted")


def test_a_department_that_is_not_in_the_rule_files_has_no_scope(ruleset: Ruleset):
    """RBAC-03, fail closed: an unknown department is an error, never an empty or a full scope."""
    for department in ("auditors", "", "SALES", "org-1a2b3c"):
        with pytest.raises(policy().PolicyError):
            policy().scope_of(viewer("x", department=department), ruleset)


def test_a_member_without_a_department_has_no_scope(ruleset: Ruleset):
    """RBAC-03, fail closed."""
    with pytest.raises(policy().PolicyError):
        policy().scope_of(viewer("x", department=None), ruleset)


def test_a_role_nobody_defined_has_no_scope(ruleset: Ruleset):
    """auth-rbac §1.2: an unknown role never counts as an administrator."""
    stranger = viewer("sales", role="owner")
    with pytest.raises(policy().PolicyError):
        policy().scope_of(stranger, ruleset)
    assert policy().can_manage(stranger) is False
    try:  # refused either way: "no", or an error
        assert policy().can_upload(stranger, ruleset) is False
    except policy().PolicyError:
        pass


def test_policy_errors_are_permission_errors():
    """A forgotten handler turns a policy error into a refusal, not into data."""
    assert issubclass(policy().PolicyError, PermissionError)


def test_a_scope_has_no_defaults():
    """auth-rbac §3: a scope is never built by accident: both parts must be said."""
    with pytest.raises(TypeError):
        policy().Scope()
    with pytest.raises(TypeError):
        policy().Scope(cost_visible=True)
    with pytest.raises(TypeError):
        policy().Scope(areas=None)


def test_a_scope_and_a_viewer_cannot_be_changed(ruleset: Ruleset):
    """auth-rbac §3: both are frozen; a request cannot widen the scope it was given."""
    scope = scope_for("quality", ruleset)
    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.areas = None
    with pytest.raises(dataclasses.FrozenInstanceError):
        viewer("quality").role = "admin"


def test_every_area_without_cost_is_not_the_full_scope():
    """INV-8, fail closed: "all areas" that may not see cost must not get the canonical PDF or the
    unfiltered sentences."""
    assert policy().Scope(cost_visible=False, areas=None).is_full is False
    assert policy().Scope(cost_visible=True, areas=frozenset({"quality"})).is_full is False


def test_the_receiving_department_is_read_from_the_rules(ruleset: Ruleset):
    """auth-rbac §2: ``receives_all`` and ``cost_visible`` come from the rule files, not from names."""
    changed = ruleset.model_copy(
        update={
            "departments": tuple(
                d.model_copy(update={"receives_all": d.id == "quality", "cost_visible": d.id == "legal"})
                for d in ruleset.departments
            )
        }
    )
    quality, sales, legal = (scope_for(n, changed) for n in ("quality", "sales", "legal"))
    assert quality.cost_visible is False and quality.is_full is False
    assert quality.areas is None or quality.areas == frozenset(changed.department_ids)
    assert sales.areas == frozenset({"sales"}) and sales.cost_visible is False
    assert legal.cost_visible is True and legal.areas == frozenset({"legal"})
    assert policy().can_upload(viewer("quality"), changed) is True
    assert policy().can_upload(viewer("sales"), changed) is False


def test_only_admin_and_the_receiving_department_may_upload(ruleset: Ruleset):
    """auth-rbac §2."""
    allowed = {name for name in ACCOUNTS if policy().can_upload(viewer(name), ruleset)}
    assert allowed == set(FULL)


def test_only_admin_manages_accounts():
    """auth-rbac §2."""
    assert {name for name in ACCOUNTS if policy().can_manage(viewer(name))} == {"admin"}
