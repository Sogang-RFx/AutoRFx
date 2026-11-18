"""Rule files (SPEC §5).

Domain knowledge lives in ``data/rules/*.yaml`` and is edited by the domain
team. Code reads it through :func:`load_ruleset`; department lists, keywords,
requirement-strength expressions, red-flag patterns, entities and the
Go/No-Go rule are never written into classifiers or prompts (principle 8).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from autorfx.models import DecisionStatus, Group, Model, Priority, Severity

RULE_FILES = ("departments", "priority", "red_flags", "entities", "glossary", "sensitivity", "cost_model")

# Same list, same order, as COST_FIELDS in web/src/lib/rfq/contract.ts.
COST_FIELDS = (
    "currency",
    "min_quantity",
    "fixed_costs",
    "variable_cost_per_unit",
    "target_price",
    "annual_reduction_rate",
    "gross_weight",
    "net_weight",
    "material_price",
    "scrap_credit",
    "machine_hour_rate",
    "strokes_per_minute",
    "parts_per_stroke",
    "oee",
    "surface_cost",
    "packaging_cost",
    "freight_cost",
    "warehousing_cost",
    "scrap_rate",
    "sga_rate",
    "profit_rate",
    "thickness",
    "incoterm",
    "payment_days",
)

DecisionCondition = Literal[
    "no_target", "missing_inputs", "all_within_target", "all_above_target", "straddles_target"
]


class RulesError(ValueError):
    """A rule file is missing or malformed. The message names the file."""


def _compile_all(patterns: tuple[str, ...]) -> None:
    for pattern in patterns:
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"정규식 오류 '{pattern}': {exc}") from exc


class Terms(Model):
    """Words or patterns per language."""

    en: tuple[str, ...] = ()
    de: tuple[str, ...] = ()
    ko: tuple[str, ...] = ()

    def all(self) -> tuple[str, ...]:
        return self.en + self.de + self.ko


class Patterns(Terms):
    """Case-insensitive regular expressions per language, checked at load time."""

    @model_validator(mode="after")
    def _valid_regex(self) -> Patterns:
        _compile_all(self.all())
        return self


class DepartmentRule(Model):
    id: str = Field(pattern=r"^[a-z][a-z_]*$")
    label: str
    team_label: str
    role: str
    """What the department does with an RFQ; given to the classifier as text."""
    receives: str
    """Which clauses it receives."""
    receives_all: bool = False
    cost_visible: bool = False
    keywords: Terms = Terms()


class PriorityRule(Model):
    level: Priority
    expressions: Terms


class RedFlagRule(Model):
    type: str = Field(pattern=r"^[a-z][a-z_]*$")
    label: str
    description: str
    patterns: Patterns
    unless: Patterns = Patterns()
    """Expressions that cancel the flag, e.g. a stated cap."""
    departments: tuple[str, ...] = ()


class EntityRule(Model):
    """A value that may be stated in more than one place of a package (CONSIST-01)."""

    id: str = Field(pattern=r"^[a-z][a-z_]*$")
    label: str
    kind: Literal["number", "money", "percent", "date", "code", "text"]
    unit: str | None = None
    units: dict[str, str] = Field(default_factory=dict)
    """Unit alias → factor to the canonical unit, as a decimal string."""
    labels: Terms = Terms()
    """Words that introduce the value in text, or head its column in a table."""
    patterns: Patterns = Patterns()
    """Optional expressions with a named group ``value`` (and optionally ``unit`` or ``key``)."""
    codes: tuple[str, ...] = ()
    compare: Literal["equal", "tolerance", "month", "category"] = "equal"
    """How two normalized values are compared: exactly, within ``tolerance``, by year and
    month, or by membership of the same code."""
    tolerance: str | None = None
    """Absolute tolerance in the canonical unit, as a decimal string."""
    key: str | None = None
    """Qualifier that must be equal before two mentions are compared, e.g. the test standard."""
    scope: Literal["part", "package"] = "package"
    department: str
    severity: Severity = "medium"
    affects_cost: bool = False
    cost_fields: tuple[str, ...] = ()

    @field_validator("cost_fields")
    @classmethod
    def _known_cost_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [f for f in value if f not in COST_FIELDS]
        if unknown:
            raise ValueError(f"알 수 없는 원가 입력: {unknown}")
        return value


class GlossaryEntry(Model):
    terms: tuple[str, ...] = Field(min_length=1)
    """What a user may type, usually Korean or English."""
    expands_to: tuple[str, ...] = Field(min_length=1)
    """Words added to the query, in the languages of the documents."""


class SensitivityRules(Model):
    cost_patterns: Patterns
    cost_titles: Terms = Terms()
    cost_entities: tuple[str, ...] = ()
    """Entity ids whose mention makes a clause cost-sensitive."""
    boilerplate_patterns: tuple[str, ...] = ()
    boilerplate_department: str | None = None

    @field_validator("boilerplate_patterns")
    @classmethod
    def _valid_regex(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _compile_all(value)
        return value


class DriverRule(Model):
    """How an unresolved conflict on one entity changes the cost breakdown."""

    entity: str
    scales: tuple[str, ...] = ()
    """Breakdown fields multiplied by (chosen value / reference value)."""
    includes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    """Code value → breakdown fields that count; the fields named by other codes become zero."""


class DecisionRule(Model):
    id: str
    when: DecisionCondition
    status: DecisionStatus
    note: str


class CostModelRules(Model):
    assumption_note: str = ""
    margin: str = "0"
    """Headroom required below the target price, as a fraction."""
    drivers: tuple[DriverRule, ...] = ()
    decisions: tuple[DecisionRule, ...] = Field(min_length=1)
    example_assumptions: dict[str, str] = Field(default_factory=dict)

    @field_validator("example_assumptions")
    @classmethod
    def _known_fields(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = [f for f in value if f not in COST_FIELDS]
        if unknown:
            raise ValueError(f"알 수 없는 원가 입력: {unknown}")
        return value


class Ruleset(Model):
    departments: tuple[DepartmentRule, ...] = Field(min_length=1)
    priority: tuple[PriorityRule, ...]
    red_flags: tuple[RedFlagRule, ...]
    entities: tuple[EntityRule, ...]
    glossary: tuple[GlossaryEntry, ...]
    sensitivity: SensitivityRules
    cost_model: CostModelRules
    assumption_note: str = ""
    digest: str
    """SHA-256 over the rule files' content; part of every analysis cache key."""

    @model_validator(mode="after")
    def _consistent(self) -> Ruleset:
        ids = [d.id for d in self.departments]
        if len(set(ids)) != len(ids):
            raise ValueError("부서 id가 중복됩니다")
        levels = sorted(p.level for p in self.priority)
        if levels != ["info", "must", "optional", "should"]:
            raise ValueError("priority.yaml에는 must, should, optional, info가 한 번씩 있어야 합니다")
        known = set(ids)
        for flag in self.red_flags:
            if unknown := set(flag.departments) - known:
                raise ValueError(f"red_flags.yaml '{flag.type}': 없는 부서 {sorted(unknown)}")
        entity_ids = {e.id for e in self.entities}
        for entity in self.entities:
            if entity.department not in known:
                raise ValueError(f"entities.yaml '{entity.id}': 없는 부서 '{entity.department}'")
        if unknown := set(self.sensitivity.cost_entities) - entity_ids:
            raise ValueError(f"sensitivity.yaml: 없는 엔터티 {sorted(unknown)}")
        dept = self.sensitivity.boilerplate_department
        if dept is not None and dept not in known:
            raise ValueError(f"sensitivity.yaml: 없는 부서 '{dept}'")
        if unknown := {d.entity for d in self.cost_model.drivers} - entity_ids:
            raise ValueError(f"cost_model.yaml: 없는 엔터티 {sorted(unknown)}")
        return self

    @property
    def department_ids(self) -> tuple[str, ...]:
        return tuple(d.id for d in self.departments)

    def department(self, department_id: str) -> DepartmentRule | None:
        return next((d for d in self.departments if d.id == department_id), None)

    def group_of(self, team: str | None) -> Group:
        """Group of a team. Unknown or missing teams are restricted (RBAC-03)."""
        rule = self.department(team) if team else None
        return "cost_visible" if rule is not None and rule.cost_visible else "cost_restricted"


def _read(path: Path) -> dict:
    if not path.is_file():
        raise RulesError(f"룰셋 파일이 없습니다: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RulesError(f"{path.name}: YAML 형식 오류: {exc}") from exc
    if not isinstance(data, dict):
        raise RulesError(f"{path.name}: 최상위는 매핑이어야 합니다")
    return data


def load_ruleset(rules_dir: Path) -> Ruleset:
    """Read and validate every rule file. Raises :class:`RulesError` naming the problem."""
    raw = {name: _read(rules_dir / f"{name}.yaml") for name in RULE_FILES}
    digest = hashlib.sha256(
        json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    try:
        return Ruleset(
            departments=raw["departments"]["departments"],
            assumption_note=raw["departments"].get("assumption_note", ""),
            priority=raw["priority"]["levels"],
            red_flags=raw["red_flags"]["red_flags"],
            entities=raw["entities"]["entities"],
            glossary=raw["glossary"]["entries"],
            sensitivity=raw["sensitivity"],
            cost_model=raw["cost_model"],
            digest=digest,
        )
    except KeyError as exc:
        raise RulesError(f"룰셋에 필요한 키가 없습니다: {exc}") from exc
    except ValidationError as exc:
        raise RulesError(f"룰셋 검증 실패: {exc}") from exc
