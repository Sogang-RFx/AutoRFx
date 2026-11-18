"""Deterministic pre-cost (COST-01..03, COST-06..09, INV-5, SPEC §8).

Decimal arithmetic only. Reported values are rounded once, at the end, to four
decimals half-up; intermediates are never rounded.

    fixed_cost_per_unit = fixed_costs / min_quantity
    unit_cost           = fixed_cost_per_unit + variable_cost_per_unit

With a cost breakdown the variable cost is computed, not entered:

    material      = gross_weight * material_price - (gross_weight - net_weight) * scrap_credit
    manufacturing = machine_hour_rate / (60 * strokes_per_minute * parts_per_stroke * oee)
    logistics     = packaging_cost + freight_cost + warehousing_cost
    piece_cost    = (material + manufacturing + surface_cost + logistics) * (1 + scrap_rate) * (1 + sga_rate)
    piece_price   = piece_cost * (1 + profit_rate)

The variable cost is ``piece_cost``: the minimum quote is the cost with no
profit. A cost-related conflict that is not resolved is evaluated for every
value it could take (``cost_model.yaml`` says how a value changes the
breakdown), which gives a cost range instead of one figure. The decision rule
then looks at the whole range (INV-13): it is "go" or "no_go" only when every
case agrees.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import product

from autorfx.models import BreakdownInput, ConsistencyIssue, Model, PrecostInput
from autorfx.rules import DriverRule, Ruleset

PLACES = Decimal("0.0001")
MAX_SCENARIOS = 16
SCALED = ("gross_weight", "net_weight")
LINES = (
    ("material_cost", "재료비"),
    ("manufacturing_cost", "가공비"),
    ("surface_cost", "표면처리비"),
    ("logistics_cost", "포장·물류비"),
    ("overhead_cost", "불량·판관비 가산"),
)


class PrecostInputError(ValueError):
    """The inputs cannot be computed. The message is shown to the user; no result is returned."""


class RfqCostContext(Model):
    """What the RFQ itself contributes to a calculation."""

    issues: tuple[ConsistencyIssue, ...] = ()
    annual_reduction_rate: Decimal | None = None
    """Fraction per year, e.g. 0.03."""
    reduction_starts_at_index: int = 1
    """Index of the first delivery year that is reduced; 1 means "from the second year"."""
    years: tuple[int, ...] = ()


def money(value: Decimal) -> str:
    """A reported amount: four decimals, half-up, plain notation."""
    return format(value.quantize(PLACES, rounding=ROUND_HALF_UP), "f")


def exact(value: Decimal) -> str:
    """An input echoed back: as entered, without exponent."""
    return format(value, "f")


def breakdown_costs(b: BreakdownInput) -> dict[str, Decimal]:
    """Line items of the cost breakdown, unrounded."""
    if b.gross_weight < b.net_weight:
        raise PrecostInputError("투입 중량이 순중량보다 작습니다.")
    material = b.gross_weight * b.material_price - (b.gross_weight - b.net_weight) * b.scrap_credit
    if material < 0:
        raise PrecostInputError("스크랩 환입액이 재료비보다 큽니다.")
    manufacturing = b.machine_hour_rate / (Decimal(60) * b.strokes_per_minute * b.parts_per_stroke * b.oee)
    logistics = b.packaging_cost + b.freight_cost + b.warehousing_cost
    direct = material + manufacturing + b.surface_cost + logistics
    piece_cost = direct * (1 + b.scrap_rate) * (1 + b.sga_rate)
    return {
        "material_cost": material,
        "manufacturing_cost": manufacturing,
        "surface_cost": b.surface_cost,
        "logistics_cost": logistics,
        "overhead_cost": piece_cost - direct,
        "piece_cost": piece_cost,
        "piece_price": piece_cost * (1 + b.profit_rate),
    }


def _apply(breakdown: BreakdownInput, driver: DriverRule, base: str, chosen: str) -> BreakdownInput:
    """The breakdown as it would be if the driver's entity had the value ``chosen`` instead of ``base``."""
    update: dict[str, Decimal] = {}
    if driver.scales and chosen != base:
        try:
            factor = Decimal(chosen) / Decimal(base)
        except (InvalidOperation, ZeroDivisionError) as exc:
            raise PrecostInputError(
                f"'{driver.entity}'의 값으로 계산할 수 없습니다: {base}, {chosen}"
            ) from exc
        update.update({field: getattr(breakdown, field) * factor for field in driver.scales})
    if chosen in driver.includes:
        counted = set(driver.includes[chosen])
        every = {field for fields in driver.includes.values() for field in fields}
        update.update({field: Decimal(0) for field in every - counted})
    return breakdown.model_copy(update=update) if update else breakdown


Dimension = tuple[ConsistencyIssue, DriverRule, str, tuple[str, ...]]


def _dimensions(
    context: RfqCostContext, resolved: Mapping[str, str], ruleset: Ruleset
) -> tuple[list[Dimension], list[ConsistencyIssue]]:
    """Cost conflicts: those a driver can evaluate, and open ones it cannot (INV-13).

    A driver dimension is the issue, its rule, the base value and the values still possible. A
    conflict on an entity with no driver (target price, volume, currency) cannot be evaluated per
    value, so while it is open no decision is made; choosing a value in ``resolved`` closes it.
    """
    drivers = {d.entity: d for d in ruleset.cost_model.drivers}
    conflicts = {
        issue.id: issue for issue in context.issues if issue.category == "conflict" and issue.affects_cost
    }
    unknown = sorted(set(resolved) - set(conflicts))
    if unknown:
        raise PrecostInputError(f"해소할 수 있는 원가 충돌이 아닙니다: {', '.join(unknown)}")
    evaluated: list[Dimension] = []
    unmodelled: list[ConsistencyIssue] = []
    for issue in conflicts.values():
        values = tuple(dict.fromkeys(e.normalized for e in issue.evidence if e.normalized))
        chosen = resolved.get(issue.id)
        if chosen is not None and chosen not in values:
            raise PrecostInputError(f"'{issue.id}'에 고를 수 있는 값은 {', '.join(values)}입니다.")
        driver = drivers.get(issue.entity or "")
        if driver is not None and len(values) >= 2:
            evaluated.append((issue, driver, values[0], (chosen,) if chosen is not None else values))
        elif chosen is None:
            unmodelled.append(issue)
    return evaluated, unmodelled


def _decide(target: Decimal | None, low: Decimal, high: Decimal, blocked: bool, ruleset: Ruleset) -> dict:
    model = ruleset.cost_model
    limit = target * (1 - Decimal(model.margin)) if target is not None else None
    met = {
        "no_target": limit is None,
        "missing_inputs": blocked,
        "all_within_target": limit is not None and not blocked and high <= limit,
        "all_above_target": limit is not None and not blocked and low > limit,
        "straddles_target": limit is not None and not blocked,
    }
    rule = next(r for r in model.decisions if met[r.when])
    return {
        "status": rule.status,
        "note": rule.note,
        "target_price": exact(target) if target is not None else None,
        "gap": money(limit - high) if limit is not None else None,
        "rule": rule.id,
    }


def compute(
    inputs: PrecostInput,
    *,
    breakdown: BreakdownInput | None,
    context: RfqCostContext,
    resolved: Mapping[str, str],
    ruleset: Ruleset,
) -> dict:
    """The ``data`` object of ``contract/precost.schema.json``."""
    fixed = inputs.fixed_costs / Decimal(inputs.min_quantity)
    dimensions, unmodelled = _dimensions(context, resolved, ruleset)
    open_dimensions = [d for d in dimensions if len(d[3]) > 1]
    blocked = (bool(open_dimensions) and breakdown is None) or bool(unmodelled)

    def unit_cost(choice: Sequence[str]) -> tuple[Decimal, dict[str, Decimal] | None]:
        if breakdown is None:
            return fixed + inputs.variable_cost_per_unit, None
        adjusted = breakdown
        for (_, driver, base, _), value in zip(dimensions, choice, strict=True):
            adjusted = _apply(adjusted, driver, base, value)
        costs = breakdown_costs(adjusted)
        return fixed + costs["piece_cost"], costs

    combos = list(product(*(values for *_, values in dimensions))) if breakdown is not None else [()]
    if len(combos) > MAX_SCENARIOS:
        raise PrecostInputError(
            f"해소되지 않은 충돌의 조합이 {len(combos)}가지입니다. 먼저 일부를 해소해 주세요."
        )
    base_choice = tuple(values[0] if len(values) > 1 else values[0] for *_, values in dimensions)
    head_cost, head_lines = unit_cost(base_choice if breakdown is not None else ())
    evaluated = [(choice, unit_cost(choice)[0]) for choice in combos]
    low, high = min(c for _, c in evaluated), max(c for _, c in evaluated)

    scenarios = []
    if open_dimensions and breakdown is not None:
        for choice, cost in evaluated:
            picks = [
                {"issue_id": issue.id, "value": value}
                for (issue, _, _, values), value in zip(dimensions, choice, strict=True)
                if len(values) > 1
            ]
            scenarios.append(
                {"label": " · ".join(p["value"] for p in picks), "choices": picks, "unit_cost": money(cost)}
            )

    decision = _decide(inputs.target_price, low, high, blocked, ruleset)
    reasons = [f"해소되지 않은 충돌: {issue.title}" for issue, *_ in open_dimensions]
    reasons += [
        f"해소되지 않은 충돌: {issue.title} (원가에 주는 영향을 자동으로 계산할 수 없어 값을 골라야 합니다)"
        for issue in unmodelled
    ]
    if open_dimensions and breakdown is None:
        reasons.append("원가분해 입력이 있어야 충돌이 원가에 주는 영향을 계산할 수 있습니다.")
    if scenarios:
        worst = max(scenarios, key=lambda s: Decimal(s["unit_cost"]))
        reasons.append(f"가장 불리한 경우: {worst['label']} → {worst['unit_cost']} {inputs.currency}")

    yearly = []
    rate, target = context.annual_reduction_rate, inputs.target_price
    if rate is not None and target is not None:
        for index, year in enumerate(context.years):
            steps = max(0, index - context.reduction_starts_at_index + 1)
            price = target * (1 - rate) ** steps
            yearly.append({"year": year, "price": money(price), "below_cost": price < high})
        first = next((y for y in yearly if y["below_cost"]), None)
        if first:
            reasons.append(f"연간 단가 인하를 적용하면 {first['year']}년부터 목표가가 원가보다 낮아집니다.")

    variable = head_lines["piece_cost"] if head_lines else inputs.variable_cost_per_unit
    return {
        "inputs": {
            "item": inputs.item,
            "min_quantity": inputs.min_quantity,
            "fixed_costs": exact(inputs.fixed_costs),
            "variable_cost_per_unit": money(variable) if head_lines else exact(variable),
            "currency": inputs.currency,
            "target_price": exact(target) if target is not None else None,
        },
        "breakdown": {
            "lines": [{"key": key, "label": label, "value": money(head_lines[key])} for key, label in LINES],
            "piece_cost": money(head_lines["piece_cost"]),
            "piece_price": money(head_lines["piece_price"]),
        }
        if head_lines
        else None,
        "fixed_cost_per_unit": money(fixed),
        "unit_cost": money(head_cost),
        "scenarios": scenarios,
        "range": {"low": money(low), "high": money(high)},
        "yearly": yearly,
        "decision": {**decision, "reasons": reasons},
    }
