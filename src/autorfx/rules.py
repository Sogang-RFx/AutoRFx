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
from pydantic import Field, PrivateAttr, ValidationError, field_validator, model_validator

from autorfx.models import Group, Model, Priority, Severity

RULE_FILES = (
    "departments",
    "priority",
    "red_flags",
    "entities",
    "glossary",
    "sensitivity",
    "cost_model",
    "work_areas",
    "price_sheet",
)

# Same list, same order, as COST_FIELDS in web/src/lib/rfq/contract.ts.
# What the RFQ itself can state for a pre-cost; everything else is the supplier's own input.
COST_FIELDS = (
    "currency",
    "volume",
    "target_price",
    "annual_reduction_rate",
    "payment_days",
    "incoterm",
    "thickness",
    "net_weight",
    "material",
)

ITEM_CATEGORIES = (
    "material",
    "process",
    "labor",
    "purchased",
    "outsourced",
    "logistics",
    "freight",
    "other",
    "credit",
)


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


class RfqTypeRule(Model):
    id: str = Field(pattern=r"^[a-z][a-z_]*$")
    label: str
    decision_unit: Literal["rfq", "item"] = "rfq"
    baseline: Literal["target_price", "none"] = "target_price"
    automatic: bool = True
    """False: the type is recognised but never judged automatically (manual pre-cost)."""
    cost_basis: Literal["manufacturing", "resale"] = "manufacturing"
    """resale: bought-in goods sold on; no scrap or SG&A uplift, operating costs are entered as items."""
    patterns: Patterns


class RfqTypeRules(Model):
    min_hits: int = Field(default=3, ge=1)
    types: tuple[RfqTypeRule, ...] = Field(min_length=1)


class RequirementRule(Model):
    """A qualification or contract condition checked before any cost is computed (knockout 1)."""

    id: str = Field(pattern=r"^[a-z][a-z_]*$")
    label: str
    department: str
    kind: Literal["fact", "judgment"]
    question: str
    profile_field: Literal["certifications", "qualifications", "processes"] | None = None
    patterns: Patterns = Patterns()
    named_patterns: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    """Process name → expressions; the name is what is asked and compared."""

    @field_validator("named_patterns")
    @classmethod
    def _valid_named(cls, value: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        for patterns in value.values():
            _compile_all(patterns)
        return value


class DriverCount(Model):
    category: str
    when: tuple[str, ...]


class DriverRule(Model):
    """How an unresolved conflict on one entity changes the cost items."""

    entity: str
    scales: tuple[str, ...] = ()
    """Item categories whose quantity is multiplied by (chosen value / reference value)."""
    counts: DriverCount | None = None
    """Items of this category count only when the chosen code is listed."""


class ShockRule(Model):
    id: str = Field(pattern=r"^[a-z][a-z_]*$")
    label: str
    size: str = "0"
    categories: dict[str, str] = Field(default_factory=dict)
    rate_points: str | None = None
    applies_when: Literal["always", "foreign_currency"] = "always"
    passed_through_by: str | None = None


class PassThroughRule(Model):
    label: str
    patterns: Patterns


class ScoreBand(Model):
    min: int = Field(ge=0, le=100)
    status: Literal["go", "conditional", "no_go"]


class ProfitabilityRule(Model):
    at_min_quote: int = Field(ge=0, le=100)
    at_safe_quote: int = Field(ge=0, le=100)


class ScoringRules(Model):
    weights: dict[str, int]
    bands: tuple[ScoreBand, ...] = Field(min_length=1)
    profitability: ProfitabilityRule
    contract_risk_per_flag: int = Field(ge=0, le=100)
    payment_curve: tuple[tuple[int, int], ...] = Field(min_length=2)
    deferred_tooling_penalty: int = Field(ge=0, le=100)
    capacity_curve: tuple[tuple[str, int], ...] = Field(min_length=2)
    neutral: int = Field(ge=0, le=100)

    @field_validator("weights")
    @classmethod
    def _weights(cls, value: dict[str, int]) -> dict[str, int]:
        expected = {"profitability", "contract_risk", "cash_flow", "capacity", "technical_fit", "strategic"}
        if set(value) != expected:
            raise ValueError(f"가중치 항목은 {sorted(expected)}이어야 합니다")
        if sum(value.values()) != 100:
            raise ValueError("가중치의 합은 100이어야 합니다")
        return value


class CompanyDefaults(Model):
    home_currency: str = Field(pattern=r"^[A-Z]{3}$")
    discount_rate: str
    min_margin: str
    scrap_rate: str
    sga_rate: str


class CostModelRules(Model):
    assumption_note: str = ""
    rfq_types: RfqTypeRules
    requirements: tuple[RequirementRule, ...] = ()
    item_categories: dict[str, str]
    drivers: tuple[DriverRule, ...] = ()
    shocks: tuple[ShockRule, ...] = ()
    combined: tuple[str, ...] = ()
    pass_through: dict[str, PassThroughRule] = Field(default_factory=dict)
    award: dict[Literal["split", "all_or_none"], PassThroughRule] = Field(default_factory=dict)
    """How the customer awards: per line item (split) or the whole RFQ only (all_or_none)."""
    scoring: ScoringRules
    company_defaults: CompanyDefaults

    @model_validator(mode="after")
    def _consistent(self) -> CostModelRules:
        if set(self.item_categories) != set(ITEM_CATEGORIES):
            raise ValueError(f"item_categories는 {list(ITEM_CATEGORIES)}이어야 합니다")
        shock_ids = {s.id for s in self.shocks}
        if unknown := set(self.combined) - shock_ids:
            raise ValueError(f"combined: 없는 충격 {sorted(unknown)}")
        for shock in self.shocks:
            if unknown := set(shock.categories) - set(ITEM_CATEGORIES):
                raise ValueError(f"충격 '{shock.id}': 없는 원가 분류 {sorted(unknown)}")
            if shock.passed_through_by and shock.passed_through_by not in self.pass_through:
                raise ValueError(f"충격 '{shock.id}': 없는 전가 조항 '{shock.passed_through_by}'")
        for driver in self.drivers:
            used = set(driver.scales) | ({driver.counts.category} if driver.counts else set())
            if unknown := used - set(ITEM_CATEGORIES):
                raise ValueError(f"driver '{driver.entity}': 없는 원가 분류 {sorted(unknown)}")
        return self


def keyword_pattern(term: str) -> str:
    """Expression for a keyword of ``work_areas.yaml`` or ``price_sheet.yaml`` (rules in their header).

    ``re:`` is a regular expression as written; a Latin word matches as a whole word with an
    optional plural or inflection ending; Korean takes particles and compounds, so it matches as is.
    """
    if term.startswith("re:"):
        return term[3:]
    if re.search(r"[a-zäöüß]", term, re.IGNORECASE):
        return rf"(?<!\w){re.escape(term)}(?:s|es|e|n|en)?(?!\w)"
    return re.escape(term)


class WorkArea(Model):
    id: str = Field(pattern=r"^[a-z][a-z-]*$")
    label: str
    topics: dict[str, tuple[str, ...]] = Field(min_length=1)
    """The narrower topics the area groups (the earlier area ids) -> their keywords."""


class WorkAreaRules(Model):
    """Work areas a company department receives (work_areas.yaml)."""

    areas: tuple[WorkArea, ...] = Field(min_length=1)
    _patterns: dict[str, tuple[tuple[re.Pattern[str], int], ...]] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> WorkAreaRules:
        ids = [a.id for a in self.areas]
        if len(set(ids)) != len(ids):
            raise ValueError("work_areas.yaml: 영역 id가 중복됩니다")
        topics = [t for a in self.areas for t in a.topics]
        if len(set(topics)) != len(topics):
            raise ValueError("work_areas.yaml: 주제 id가 중복됩니다")
        for area in self.areas:
            compiled = []
            for terms in area.topics.values():
                for term in terms:
                    try:
                        pattern = re.compile(keyword_pattern(term), re.IGNORECASE)
                    except re.error as exc:
                        raise ValueError(f"work_areas.yaml '{area.id}': 정규식 오류 '{term}': {exc}") from exc
                    # A longer keyword is more specific: it weighs more when areas are ranked.
                    compiled.append((pattern, len(term.removeprefix("re:"))))
            self._patterns[area.id] = tuple(compiled)
        return self

    @property
    def patterns(self) -> dict[str, tuple[tuple[re.Pattern[str], int], ...]]:
        """Area id -> (compiled keyword, weight), in catalog order."""
        return self._patterns


ColumnRole = Literal["no", "part", "description", "quantity", "unit", "months", "price", "total"]
COLUMN_ROLES: tuple[ColumnRole, ...] = (
    "no",
    "part",
    "description",
    "quantity",
    "unit",
    "months",
    "price",
    "total",
)


def _compiled(terms: tuple[str, ...], where: str) -> tuple[tuple[re.Pattern[str], int], ...]:
    """(expression, weight) per keyword; a longer keyword is more specific and weighs more."""
    found = []
    for term in terms:
        try:
            found.append((re.compile(keyword_pattern(term), re.IGNORECASE), len(term.removeprefix("re:"))))
        except re.error as exc:
            raise ValueError(f"{where}: 정규식 오류 '{term}': {exc}") from exc
    return tuple(found)


class PriceSheetRules(Model):
    """Words that name the columns of an RFQ price sheet (price_sheet.yaml)."""

    roles: dict[ColumnRole, tuple[str, ...]]
    yearly: tuple[str, ...] = ()
    monthly: tuple[str, ...] = ()
    group: tuple[str, ...] = ()
    total_row: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> PriceSheetRules:
        if missing := {"quantity", "description"} - set(self.roles):
            raise ValueError(f"price_sheet.yaml: 역할 {sorted(missing)}의 단어가 없습니다")
        for role, terms in self.roles.items():
            _compiled(terms, f"price_sheet.yaml '{role}'")
        for name in ("yearly", "monthly", "group", "total_row"):
            _compiled(getattr(self, name), f"price_sheet.yaml '{name}'")
        return self

    def compiled(self, extra: dict[str, tuple[str, ...]] | None = None) -> dict[str, tuple]:
        """Compiled keywords per role, plus ``yearly``, ``monthly``, ``group`` and ``total_row``.

        ``extra`` adds approved header words per role (literal phrases, kept in the work folder).
        """
        extra = extra or {}
        found: dict[str, tuple] = {
            role: _compiled(self.roles.get(role, ()) + tuple(extra.get(role, ())), role)
            for role in COLUMN_ROLES
        }
        for name in ("yearly", "monthly", "group", "total_row"):
            found[name] = _compiled(getattr(self, name), name)
        return found


class Ruleset(Model):
    departments: tuple[DepartmentRule, ...] = Field(min_length=1)
    priority: tuple[PriorityRule, ...]
    red_flags: tuple[RedFlagRule, ...]
    entities: tuple[EntityRule, ...]
    glossary: tuple[GlossaryEntry, ...]
    sensitivity: SensitivityRules
    cost_model: CostModelRules
    work_areas: WorkAreaRules
    price_sheet: PriceSheetRules
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
        for requirement in self.cost_model.requirements:
            if requirement.department not in known:
                raise ValueError(f"cost_model.yaml '{requirement.id}': 없는 부서 '{requirement.department}'")
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
            work_areas=raw["work_areas"],
            price_sheet=raw["price_sheet"],
            digest=digest,
        )
    except KeyError as exc:
        raise RulesError(f"룰셋에 필요한 키가 없습니다: {exc}") from exc
    except ValidationError as exc:
        raise RulesError(f"룰셋 검증 실패: {exc}") from exc
