"""Deterministic pre-cost and Go/No-Go (steps ③, ⑥–⑨; SPEC §8, INV-5, INV-13).

Decimal arithmetic only; reported amounts are rounded once, at the end, to
four decimals half-up. No number here comes from a language model.

Cost of one piece — every line is quantity × unit price, whatever the process::

    direct      = Σ quantity × unit_price          (credit lines are subtracted)
    piece_cost  = direct × (1 + scrap_rate) × (1 + sga_rate)
    basis       = piece_cost + tooling / Σ volume  (only when tooling is amortized in the price)

Lifetime minimum quote — the price at SOP that still earns the minimum margin
on average once the annual reduction has been applied every year::

    LF          = Σ q_t (1 − r)^k_t / Σ q_t
    min_quote   = M + (basis × (1 + min_margin) − M) / LF

``M`` is the material part of the piece cost when raw-material prices are
passed to the customer (indexation): that part follows the market and is not
reduced. Without indexation ``M`` is 0.

The safe quote is the same formula with the combined shock applied to the
cost lines. NPV and payback are shown for reference and never decide.

Order of the decision: a failed knockout answer → no-go; a type that is not
judged automatically → not applicable; a cost conflict the rules cannot
evaluate → hold; no target price → no baseline; target below the minimum
quote in every case → no-go; cases that disagree → hold; otherwise the
weighted score and its band.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import product

from autorfx.models import ConsistencyIssue, CostItem, Model, PrecostInput, PrecostLine
from autorfx.precost.company import Company
from autorfx.rules import DriverRule, Ruleset, ShockRule

PLACES = Decimal("0.0001")
MAX_SCENARIOS = 16
ONE = Decimal(1)
STATUS_LABEL = {
    "go": "Go",
    "conditional": "조건부 Go",
    "no_go": "No-Go",
    "hold": "보류",
    "baseline_undefined": "기준 없음",
    "not_applicable": "자동 판정 대상 아님",
}


class PrecostInputError(ValueError):
    """The inputs cannot be computed. The message is shown to the user; no result is returned."""


class RfqCostContext(Model):
    """What the analysed RFQ contributes to a calculation (empty without an RFQ)."""

    issues: tuple[ConsistencyIssue, ...] = ()
    classification: dict | None = None
    requirements: tuple[dict, ...] = ()
    pass_through: tuple[str, ...] = ()
    """Ids of pass-through clauses found in the RFQ (indexation, fx_adjustment)."""
    red_flag_types: tuple[str, ...] = ()
    award: str | None = None
    """How the RFQ awards (split | all_or_none | either), None when it does not say."""


def D(text: str) -> Decimal:
    return Decimal(text)


def money(value: Decimal) -> str:
    """A reported amount: four decimals, half-up, plain notation."""
    return format(value.quantize(PLACES, rounding=ROUND_HALF_UP), "f")


def _grouped(value: Decimal) -> str:
    """An amount for a sentence: thousands grouped, two decimals."""
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}"


def signed(value: Decimal) -> str:
    return money(value)


def exact(value: Decimal) -> str:
    return format(value, "f")


def _int(value: Decimal) -> int:
    return int(value.quantize(ONE, rounding=ROUND_HALF_UP))


# ── cost of one piece ───────────────────────────────────────────────────────
def piece_costs(
    items: Sequence[CostItem],
    company: Company,
    multipliers: Mapping[str, Decimal] | None = None,
    *,
    resale: bool = False,
) -> dict[str, Decimal]:
    """Category totals, direct cost, piece cost and its material part, unrounded.

    Resale (distribution) adds no scrap or SG&A uplift: nothing is produced, and the
    operating costs of reselling are entered as items of their own.
    """
    settings = company.settings
    totals: dict[str, Decimal] = {}
    for item in items:
        factor = (multipliers or {}).get(item.category, ONE)
        value = item.quantity * item.unit_price * factor
        totals[item.category] = totals.get(item.category, Decimal(0)) + value
    credit = totals.pop("credit", Decimal(0))
    direct = sum(totals.values(), Decimal(0)) - credit
    if direct < 0:
        raise PrecostInputError("환입(차감) 항목이 다른 원가 항목의 합보다 큽니다.")
    uplift = ONE if resale else (ONE + settings.scrap_rate) * (ONE + settings.sga_rate)
    return {
        "lines": totals,
        "credit": credit,
        "direct": direct,
        "piece_cost": direct * uplift,
        "overhead": direct * uplift - direct,
        "material_part": max(totals.get("material", Decimal(0)) - credit, Decimal(0)) * uplift,
    }


def contract_total(quote: Decimal, inputs: PrecostInput) -> Decimal:
    """Whole-contract revenue at a first-year price: Σ yearly quantity × price after reductions."""
    return sum(
        (
            Decimal(v.quantity) * quote * (ONE - inputs.annual_reduction_rate) ** _steps(i, inputs)
            for i, v in enumerate(inputs.volumes)
        ),
        Decimal(0),
    )


def _cost_basis(context: RfqCostContext, ruleset: Ruleset) -> str:
    kind = (context.classification or {}).get("type")
    rule = next((t for t in ruleset.cost_model.rfq_types.types if t.id == kind), None)
    return rule.cost_basis if rule is not None else "manufacturing"


def lifetime_factor(inputs: PrecostInput) -> Decimal:
    total = sum((v.quantity for v in inputs.volumes), 0)
    if total <= 0:
        raise PrecostInputError("계획 물량의 합이 0입니다. 연도별 물량을 넣어 주세요.")
    reduced = sum(
        (
            Decimal(v.quantity) * (ONE - inputs.annual_reduction_rate) ** _steps(i, inputs)
            for i, v in enumerate(inputs.volumes)
        ),
        Decimal(0),
    )
    return reduced / Decimal(total)


def _steps(index: int, inputs: PrecostInput) -> int:
    return max(0, index - inputs.reduction_starts_at_index + 1)


def minimum_quote(
    piece: Mapping[str, Decimal], inputs: PrecostInput, company: Company, indexed: bool
) -> Decimal:
    total = sum(v.quantity for v in inputs.volumes)
    tooling = inputs.tooling.amount / Decimal(total) if inputs.tooling.payment == "amortized" else Decimal(0)
    basis = piece["piece_cost"] + tooling
    material = piece["material_part"] if indexed else Decimal(0)
    return material + (basis * (ONE + company.settings.min_margin) - material) / lifetime_factor(inputs)


# ── unresolved conflicts (INV-13) ───────────────────────────────────────────
Dimension = tuple[ConsistencyIssue, DriverRule, str, tuple[str, ...]]


def _dimensions(
    issues: Sequence[ConsistencyIssue], resolved: Mapping[str, str], ruleset: Ruleset
) -> tuple[list[Dimension], list[ConsistencyIssue]]:
    """Cost conflicts a driver can evaluate per value, and open ones it cannot.

    The quantities the user entered are taken to match the first value the RFQ states,
    whichever value is chosen in ``resolved``; other values scale or drop lines.
    """
    drivers = {d.entity: d for d in ruleset.cost_model.drivers}
    conflicts = {i.id: i for i in issues if i.category == "conflict" and i.affects_cost}
    if unknown := sorted(set(resolved) - set(conflicts)):
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
            base = values[0]
            evaluated.append((issue, driver, base, (chosen,) if chosen is not None else values))
        elif chosen is None:
            unmodelled.append(issue)
    return evaluated, unmodelled


def _apply(items: Sequence[CostItem], driver: DriverRule, base: str, chosen: str) -> list[CostItem]:
    result = list(items)
    if driver.scales and chosen != base:
        try:
            factor = Decimal(chosen) / Decimal(base)
        except (InvalidOperation, ZeroDivisionError) as exc:
            raise PrecostInputError(
                f"'{driver.entity}'의 값으로 계산할 수 없습니다: {base}, {chosen}"
            ) from exc
        result = [
            i.model_copy(update={"quantity": i.quantity * factor}) if i.category in driver.scales else i
            for i in result
        ]
    if driver.counts is not None and chosen not in driver.counts.when:
        result = [i for i in result if i.category != driver.counts.category]
    return result


# ── shocks ──────────────────────────────────────────────────────────────────
def _shock_state(
    shock: ShockRule, inputs: PrecostInput, company: Company, passed: set[str], ruleset: Ruleset
) -> str | None:
    """Why a shock does not apply, or None when it does."""
    if shock.applies_when == "foreign_currency" and inputs.currency == company.settings.home_currency:
        return f"견적 통화({inputs.currency})가 회사 기준 통화와 같아 적용하지 않습니다."
    if shock.passed_through_by and shock.passed_through_by in passed:
        label = ruleset.cost_model.pass_through[shock.passed_through_by].label
        return f"{label}으로 고객에게 전가되어 적용하지 않습니다."
    return None


def _multipliers(shocks: Sequence[ShockRule]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for shock in shocks:
        size = Decimal(shock.size)
        for category, weight in shock.categories.items():
            result[category] = result.get(category, ONE) * (ONE + size * Decimal(weight))
    return result


# ── cash flow ───────────────────────────────────────────────────────────────
def cash_flows(inputs: PrecostInput, price: Decimal, piece_cost: Decimal) -> list[tuple[int, int, Decimal]]:
    """(t, calendar year, cash flow): t = 0 is the year before SOP, when the tooling is built."""
    first = inputs.volumes[0].year
    flows: dict[int, Decimal] = {0: -inputs.tooling.amount}
    for index, volume in enumerate(inputs.volumes, start=1):
        year_price = price * (ONE - inputs.annual_reduction_rate) ** _steps(index - 1, inputs)
        flows[index] = flows.get(index, Decimal(0)) + (year_price - piece_cost) * volume.quantity
    if inputs.tooling.amount and inputs.tooling.payment in ("lump_sum", "deferred"):
        when = 1 + (inputs.tooling.deferred_years if inputs.tooling.payment == "deferred" else 0)
        flows[when] = flows.get(when, Decimal(0)) + inputs.tooling.amount
    return [(t, first - 1 + t, flows[t]) for t in sorted(flows)]


def npv(flows: Sequence[tuple[int, int, Decimal]], rate: Decimal) -> Decimal:
    return sum((cf / (ONE + rate) ** t for t, _, cf in flows), Decimal(0))


def payback(flows: Sequence[tuple[int, int, Decimal]]) -> int | None:
    running = Decimal(0)
    for t, year, cf in flows:
        running += cf
        if t > 0 and running >= 0:
            return year
    return None


# ── knockout 1 ──────────────────────────────────────────────────────────────
def answer_requirements(requirements: Sequence[dict], answers: Mapping[str, str]) -> tuple[list[dict], str]:
    """Apply the user's answers; "no" knocks out, "unknown" or no answer leaves it open."""
    checks = []
    for check in requirements:
        if check["status"] == "ask":
            answer = answers.get(check["key"])
            status = {"yes": "pass", "no": "fail"}.get(answer or "", "unknown")
            check = {**check, "status": status, "source": "answer" if answer else None, "answer": answer}
        else:
            check = {**check, "answer": None}
        checks.append(check)
    if any(c["status"] == "fail" for c in checks):
        overall = "failed"
    elif any(c["status"] == "unknown" for c in checks):
        overall = "open"
    else:
        overall = "passed"
    return checks, overall


# ── score ───────────────────────────────────────────────────────────────────
def _curve(points: Sequence[tuple[Decimal, Decimal]], x: Decimal) -> Decimal:
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def score(
    *,
    target: Decimal,
    min_quote: Decimal,
    safe_quote: Decimal,
    inputs: PrecostInput,
    checks: Sequence[dict],
    red_flag_types: Sequence[str],
    ruleset: Ruleset,
) -> dict:
    rules = ruleset.cost_model.scoring
    neutral = Decimal(rules.neutral)
    items: list[dict] = []

    p = rules.profitability
    lo, hi = Decimal(p.at_min_quote), Decimal(p.at_safe_quote)
    if target < min_quote:
        profit = Decimal(0)
    elif safe_quote <= min_quote:
        profit = hi
    else:
        profit = min(Decimal(100), lo + (hi - lo) * (target - min_quote) / (safe_quote - min_quote))
    items.append(
        (
            "profitability",
            "수익성",
            profit,
            f"목표가 {exact(target)} / 최소견적 {money(min_quote)} / 안전견적 {money(safe_quote)}",
        )
    )

    flags = len(set(red_flag_types))
    risk = max(Decimal(0), Decimal(100 - rules.contract_risk_per_flag * flags))
    items.append(("contract_risk", "계약 리스크", risk, f"독소조항 유형 {flags}개"))

    if inputs.payment_days is None:
        cash, note = neutral, "결제 기한 미입력"
    else:
        curve = [(Decimal(x), Decimal(y)) for x, y in rules.payment_curve]
        cash, note = _curve(curve, Decimal(inputs.payment_days)), f"결제 {inputs.payment_days}일"
    if inputs.tooling.amount and inputs.tooling.payment == "deferred":
        cash = max(Decimal(0), cash - rules.deferred_tooling_penalty)
        note += f", 금형비 {inputs.tooling.deferred_years}년 후 회수"
    items.append(("cash_flow", "현금흐름", cash, note))

    if inputs.capacity_utilization is None:
        capacity, note = neutral, "캐파 사용률 미입력"
    else:
        curve = [(Decimal(x), Decimal(y)) for x, y in rules.capacity_curve]
        capacity = _curve(curve, inputs.capacity_utilization)
        note = f"캐파 사용률 {_int(inputs.capacity_utilization * 100)} %"
    items.append(("capacity", "생산능력", capacity, note))

    processes = [c for c in checks if c["requirement"] == "process"]
    if not processes:
        fit, note = neutral, "RFQ에서 공정 요구를 찾지 못함"
    elif all(c["status"] == "pass" for c in processes):
        fit, note = Decimal(100), "요구 공정 모두 보유"
    else:
        fit, note = neutral, "보유 여부를 모르는 공정이 있음"
    items.append(("technical_fit", "기술 적합성", fit, note))

    strategic = Decimal(inputs.strategic) if inputs.strategic is not None else neutral
    items.append(
        (
            "strategic",
            "전략",
            strategic,
            "고객 중요도" if inputs.strategic is not None else "고객 중요도 미입력",
        )
    )

    weights = rules.weights
    total = sum((Decimal(weights[key]) * value for key, _, value, _ in items), Decimal(0)) / 100
    band = next(b for b in rules.bands if _int(total) >= b.min)
    return {
        "total": _int(total),
        "band": band.status,
        "items": [
            {"id": key, "label": label, "weight": weights[key], "score": _int(value), "note": note}
            for key, label, value, note in items
        ],
    }


# ── the whole evaluation ────────────────────────────────────────────────────
def evaluate(
    inputs: PrecostInput,
    *,
    context: RfqCostContext,
    company: Company,
    resolved: Mapping[str, str],
    ruleset: Ruleset,
    today: date,
) -> dict:
    """The ``data`` object of ``contract/precost.schema.json``."""
    if inputs.lines:
        return evaluate_lines(
            inputs, context=context, company=company, resolved=resolved, ruleset=ruleset, today=today
        )
    model = ruleset.cost_model
    settings = company.settings
    warnings: list[str] = []
    for item in inputs.items:
        if item.price_id is None:
            continue
        row = company.price(item.price_id)
        if row is None:
            raise PrecostInputError(f"'{item.label}': 단가표에 없는 행입니다. 화면을 새로 고쳐 주세요.")
        if row.currency != inputs.currency:
            raise PrecostInputError(
                f"'{item.label}': 단가표 통화({row.currency})가 견적 통화({inputs.currency})와 다릅니다. "
                "환산한 단가를 직접 넣어 주세요."
            )
        if row.valid_until < today:
            warnings.append(
                f"단가표 '{row.name}'의 유효기간({row.valid_until})이 지났습니다. "
                "담당 부서에 갱신을 요청하세요."
            )

    # ③ knockout 1
    checks, knockout = answer_requirements(context.requirements, inputs.answers)

    # ⑥ cost per case of the unresolved conflicts
    dimensions, unmodelled = _dimensions(context.issues, resolved, ruleset)
    combos = list(product(*(values for *_, values in dimensions)))
    if len(combos) > MAX_SCENARIOS:
        raise PrecostInputError(
            f"해소되지 않은 충돌의 조합이 {len(combos)}가지입니다. 먼저 일부를 해소해 주세요."
        )
    passed = set(context.pass_through)
    if inputs.material_indexed is not None:
        passed = (passed - {"indexation"}) | ({"indexation"} if inputs.material_indexed else set())
    if inputs.fx_adjusted is not None:
        passed = (passed - {"fx_adjustment"}) | ({"fx_adjustment"} if inputs.fx_adjusted else set())
    indexed = "indexation" in passed

    resale = _cost_basis(context, ruleset) == "resale"

    def items_for(choice: Sequence[str]) -> list[CostItem]:
        result = list(inputs.items)
        for (_, driver, base, _), value in zip(dimensions, choice, strict=True):
            result = _apply(result, driver, base, value)
        return result

    cases = []
    for choice in combos:
        items = items_for(choice)
        piece = piece_costs(items, company, resale=resale)
        cases.append((choice, items, piece, minimum_quote(piece, inputs, company, indexed)))
    worst_choice, worst_items, worst_piece, worst_min = max(cases, key=lambda c: c[3])
    best_min = min(c[3] for c in cases)

    scenarios = []
    if any(len(values) > 1 for *_, values in dimensions):
        for choice, _, piece, quote in cases:
            picks = [
                {"issue_id": issue.id, "value": value}
                for (issue, _, _, values), value in zip(dimensions, choice, strict=True)
                if len(values) > 1
            ]
            scenarios.append(
                {
                    "label": " · ".join(p["value"] for p in picks),
                    "choices": picks,
                    "piece_cost": money(piece["piece_cost"]),
                    "min_quote": money(quote),
                }
            )

    # ⑦ shocks on the least favourable case
    shocks = []
    applied: list[ShockRule] = []
    for shock in model.shocks:
        if shock.rate_points is not None:
            continue
        reason = _shock_state(shock, inputs, company, passed, ruleset)
        delta = None
        if reason is None:
            shocked = piece_costs(worst_items, company, _multipliers([shock]), resale=resale)
            delta = minimum_quote(shocked, inputs, company, indexed) - worst_min
            if shock.id in model.combined:
                applied.append(shock)
        shocks.append(
            {
                "id": shock.id,
                "label": shock.label,
                "applied": reason is None,
                "note": reason or "",
                "min_quote_delta": signed(delta) if delta is not None else None,
            }
        )
    combined_piece = piece_costs(worst_items, company, _multipliers(applied), resale=resale)
    safe_quote = minimum_quote(combined_piece, inputs, company, indexed)

    # NPV (reference only)
    target = inputs.target_price
    basis = target if target is not None else worst_min
    flows = cash_flows(inputs, basis, worst_piece["piece_cost"])
    value = npv(flows, settings.discount_rate)
    rate_shock = next((s for s in model.shocks if s.rate_points is not None), None)
    if rate_shock is not None:
        shifted = npv(flows, settings.discount_rate + Decimal(rate_shock.rate_points))
        shocks.append(
            {
                "id": rate_shock.id,
                "label": rate_shock.label,
                "applied": True,
                "note": f"NPV {signed(shifted - value)}",
                "min_quote_delta": None,
            }
        )
    yearly = []
    for index, volume in enumerate(inputs.volumes):
        price = basis * (ONE - inputs.annual_reduction_rate) ** _steps(index, inputs)
        yearly.append({"year": volume.year, "quantity": volume.quantity, "price": money(price)})

    lf = lifetime_factor(inputs)
    total_volume = sum(v.quantity for v in inputs.volumes)
    totals = (contract_total(worst_min, inputs), contract_total(safe_quote, inputs))
    purchased = worst_piece["lines"].get("purchased", Decimal(0))
    markup = (
        {
            "purchased": money(purchased),
            "min_quote": money(worst_min / purchased - ONE),
            "safe_quote": money(safe_quote / purchased - ONE),
        }
        if resale and purchased > 0
        else None
    )
    cost = {
        "lines": [
            {"category": key, "label": model.item_categories[key], "value": money(v)}
            for key, v in worst_piece["lines"].items()
        ]
        + (
            [
                {
                    "category": "credit",
                    "label": model.item_categories["credit"],
                    "value": money(-worst_piece["credit"]),
                }
            ]
            if worst_piece["credit"]
            else []
        )
        + (
            []
            if resale
            else [
                {"category": "overhead", "label": "불량·판관비 가산", "value": money(worst_piece["overhead"])}
            ]
        ),
        "cost_basis": "resale" if resale else "manufacturing",
        "piece_cost": money(worst_piece["piece_cost"]),
        "tooling_per_piece": money(inputs.tooling.amount / Decimal(total_volume))
        if inputs.tooling.payment == "amortized"
        else None,
        "lifetime_factor": money(lf),
        "material_indexed": indexed,
        "min_quote": money(worst_min),
        "safe_quote": money(safe_quote),
        "total_quantity": total_volume,
        "totals": {"min_quote": money(totals[0]), "safe_quote": money(totals[1])},
        "markup": markup,
        "scenarios": scenarios,
        "shocks": shocks,
        "npv": {
            "value": signed(value),
            "discount_rate": exact(settings.discount_rate),
            "price_basis": "target_price" if target is not None else "min_quote",
            "payback_year": payback(flows),
        },
        "yearly": yearly,
    }

    # ⑧–⑨ decision
    classification = context.classification
    reasons: list[str] = []
    scored = None
    failed = [c for c in checks if c["status"] == "fail"]
    open_checks = [c for c in checks if c["status"] == "unknown"]
    if failed:
        status, rule = "no_go", "knockout"
        reasons += [f"1차 넉아웃: {c['label']} — {c['question']} → 아니오" for c in failed]
    elif classification is not None and not classification.get("automatic", True):
        status, rule = "not_applicable", "rfq_type"
        reasons.append(classification.get("note", ""))
    elif unmodelled:
        status, rule = "hold", "unmodelled_conflict"
        reasons += [f"해소되지 않은 충돌: {i.title} (값을 골라야 계산할 수 있습니다)" for i in unmodelled]
    elif target is None:
        status, rule = "baseline_undefined", "no_target"
        reasons.append(
            f"목표가가 없어 판정하지 않습니다. 단위당 최소 입찰가 {money(worst_min)}, "
            f"안전 입찰가 {money(safe_quote)} {inputs.currency} "
            f"(전체 {total_volume:,}단위 총액 {_grouped(totals[0])} ~ "
            f"{_grouped(totals[1])} {inputs.currency})."
        )
    elif target < best_min:
        status, rule = "no_go", "price_knockout"
        reasons.append(
            f"2차 넉아웃: 목표가 {exact(target)}가 모든 경우의 최소견적({money(best_min)} 이상)보다 낮습니다."
        )
    elif target < worst_min:
        status, rule = "hold", "conflict_dependent"
        reasons.append(
            "문서 간 충돌을 어떻게 해소하느냐에 따라 목표가가 최소견적보다 낮아집니다. "
            "충돌을 먼저 해소해야 합니다."
        )
    else:
        scored = score(
            target=target,
            min_quote=worst_min,
            safe_quote=safe_quote,
            inputs=inputs,
            checks=checks,
            red_flag_types=context.red_flag_types,
            ruleset=ruleset,
        )
        status, rule = scored["band"], "score"
        if open_checks and status == "go":
            status = "conditional"
            reasons.append("1차 넉아웃에 '모름'으로 남은 질문이 있어 조건부로 낮췄습니다.")
    reasons += [f"판단 불가(모름): {c['question']}" for c in open_checks]
    reasons += warnings

    return {
        "item": inputs.item,
        "currency": inputs.currency,
        "classification": classification,
        "knockout": {"status": knockout, "checks": checks},
        "cost": cost,
        "score": scored,
        "decision": {
            "status": status,
            "label": STATUS_LABEL[status],
            "rule": rule,
            "reasons": reasons,
            "score": scored["total"] if scored else None,
        },
        "award": None,
        "lines": None,
        "summary": None,
    }


# ── line items ──────────────────────────────────────────────────────────────
AWARD_LABEL = {"split": "품목별 낙찰", "all_or_none": "일괄 낙찰"}
EITHER = "일괄·품목별 중 발주처가 선택 → 품목별로 판정"
BIDDABLE = {"go", "conditional", "baseline_undefined"}


def _line_input(inputs: PrecostInput, line: PrecostLine, shared_per_unit: Decimal) -> PrecostInput:
    items = line.items
    if shared_per_unit > 0:
        items = (
            *items,
            CostItem(
                label="공통비 배분", category="other", quantity=ONE, unit="개", unit_price=shared_per_unit
            ),
        )
    return PrecostInput.model_validate(
        {
            **inputs.model_dump(exclude={"lines", "award", "shared_costs"}),
            "item": line.item,
            "target_price": line.target_price,
            "volumes": [v.model_dump() for v in line.volumes],
            "items": [i.model_dump() for i in items],
            "tooling": line.tooling.model_dump(),
        }
    )


def _line_context(context: RfqCostContext, line: PrecostLine) -> RfqCostContext:
    """A line answers only the process requirements it needs; every other requirement is shared."""
    if line.processes is None:
        return context
    needed = set(line.processes)
    kept = tuple(r for r in context.requirements if r["requirement"] != "process" or r["value"] in needed)
    return context.model_copy(update={"requirements": kept})


def evaluate_lines(
    inputs: PrecostInput,
    *,
    context: RfqCostContext,
    company: Company,
    resolved: Mapping[str, str],
    ruleset: Ruleset,
    today: date,
) -> dict:
    """Each line item priced on its own, then the lines that are bid judged together.

    A split award bids only the lines the user keeps (the lines that pass are suggested);
    an all-or-none award bids every line, so one line's margin can carry another's loss.
    Shared costs are split over the bid lines by volume: a per-unit rate on every line.
    """
    stated = context.award
    # When the customer chooses after the bids, any line may be awarded alone: each must stand on its own.
    either = inputs.award is None and stated == "either"
    award = inputs.award or ("split" if either else stated) or "all_or_none"
    source = "input" if inputs.award else ("rfq" if stated else "assumed")
    bid = [line.included or award == "all_or_none" for line in inputs.lines]
    if not any(bid):
        raise PrecostInputError("입찰할 품목을 하나 이상 고르세요.")
    bid_volume = sum(
        sum(v.quantity for v in line.volumes) for line, b in zip(inputs.lines, bid, strict=True) if b
    )
    if bid_volume <= 0:
        raise PrecostInputError("입찰하는 품목의 물량 합이 0입니다.")
    shared_per_unit = inputs.shared_costs / Decimal(bid_volume)

    results = []
    for line in inputs.lines:
        line_inputs = _line_input(inputs, line, shared_per_unit)
        result = evaluate(
            line_inputs,
            context=_line_context(context, line),
            company=company,
            resolved=resolved,
            ruleset=ruleset,
            today=today,
        )
        results.append((line, line_inputs, result))

    chosen = [r for r, b in zip(results, bid, strict=True) if b]
    min_total = sum((D(res["cost"]["totals"]["min_quote"]) for _, _, res in chosen), Decimal(0))
    safe_total = sum((D(res["cost"]["totals"]["safe_quote"]) for _, _, res in chosen), Decimal(0))
    quantity = sum(res["cost"]["total_quantity"] for _, _, res in chosen)
    targets = [contract_total(li.target_price, li) for _, li, _ in chosen if li.target_price is not None]
    target_total = sum(targets, Decimal(0)) if len(targets) == len(chosen) else None

    checks, knockout = answer_requirements(context.requirements, inputs.answers)
    shared_failed = [c for c in checks if c["status"] == "fail" and c["requirement"] != "process"]
    open_checks = [c for c in checks if c["status"] == "unknown"]
    classification = context.classification
    reasons: list[str] = []
    scored = None
    line_knockouts = [res for _, _, res in chosen if res["decision"]["rule"] == "knockout"]
    holds = [res for _, _, res in chosen if res["decision"]["status"] == "hold"]

    def names(rs: Sequence[dict]) -> str:
        return ", ".join(f"'{r['item']}'" for r in rs)

    if shared_failed:
        status, rule = "no_go", "knockout"
        reasons += [f"1차 넉아웃: {c['label']} — {c['question']} → 아니오" for c in shared_failed]
    elif classification is not None and not classification.get("automatic", True):
        status, rule = "not_applicable", "rfq_type"
        reasons.append(classification.get("note", ""))
    elif line_knockouts:
        status, rule = "no_go", "knockout"
        reasons.append(
            f"공정 조건을 충족하지 못하는 품목 {names(line_knockouts)}이(가) 입찰에 들어 있습니다."
            + (" 일괄 낙찰이라 이 품목을 뺄 수 없습니다." if award == "all_or_none" else " 이 품목을 빼세요.")
        )
    elif holds:
        status, rule = "hold", holds[0]["decision"]["rule"]
        reasons.append(f"품목 {names(holds)}의 문서 간 충돌을 먼저 해소해야 합니다.")
    elif target_total is None:
        status, rule = "baseline_undefined", "no_target"
        reasons.append(
            "목표가가 없는 품목이 있어 판정하지 않습니다. "
            f"입찰 품목 {len(chosen)}개, 전체 {quantity:,}단위의 "
            f"총 견적 {_grouped(min_total)} ~ {_grouped(safe_total)} {inputs.currency}."
        )
    elif target_total < min_total:
        status, rule = "no_go", "price_knockout"
        reasons.append(
            f"2차 넉아웃: 입찰 품목 목표가 총액 {_grouped(target_total)}이 최소견적 총액 "
            f"{_grouped(min_total)}보다 낮습니다."
        )
    else:
        scored = score(
            target=target_total,
            min_quote=min_total,
            safe_quote=safe_total,
            inputs=inputs,
            checks=checks,
            red_flag_types=context.red_flag_types,
            ruleset=ruleset,
        )
        status, rule = scored["band"], "score"
        losing = [res for _, _, res in chosen if res["decision"]["rule"] == "price_knockout"]
        if losing:
            reasons.append(
                f"품목 {names(losing)}은(는) 단독으로는 목표가가 최소견적보다 낮지만, "
                "다른 품목의 마진으로 전체 합계는 최소견적을 넘습니다."
            )
        if open_checks and status == "go":
            status = "conditional"
            reasons.append("1차 넉아웃에 '모름'으로 남은 질문이 있어 조건부로 낮췄습니다.")
    reasons += [f"판단 불가(모름): {c['question']}" for c in open_checks]
    if source == "assumed":
        reasons.append("RFQ에 낙찰 방식이 없어 일괄 낙찰로 가정했습니다. 품목별 낙찰이면 바꿔 주세요.")
    if either:
        reasons.append(
            "RFQ상 발주처가 입찰 뒤 일괄·품목별 낙찰 중에서 고릅니다. 어느 품목이든 단독으로 낙찰될 수 있어 "
            "품목마다 따로 판정했습니다."
        )

    lines_out = []
    for (line, _, res), is_bid in zip(results, bid, strict=True):
        c = res["cost"]
        lines_out.append(
            {
                "item": line.item,
                "included": is_bid,
                "target_price": exact(line.target_price) if line.target_price is not None else None,
                "total_quantity": c["total_quantity"],
                "piece_cost": c["piece_cost"],
                "min_quote": c["min_quote"],
                "safe_quote": c["safe_quote"],
                "totals": c["totals"],
                "markup": c["markup"],
                "lines": c["lines"],
                "decision": res["decision"],
            }
        )
    return {
        "item": inputs.item,
        "currency": inputs.currency,
        "classification": classification,
        "knockout": {"status": knockout, "checks": checks},
        "cost": None,
        "score": scored,
        "decision": {
            "status": status,
            "label": STATUS_LABEL[status],
            "rule": rule,
            "reasons": reasons,
            "score": scored["total"] if scored else None,
        },
        "award": {"mode": award, "label": EITHER if either else AWARD_LABEL[award], "source": source},
        "lines": lines_out,
        "summary": {
            "lines_bid": len(chosen),
            "total_quantity": quantity,
            "totals": {"min_quote": money(min_total), "safe_quote": money(safe_total)},
            "target_total": money(target_total) if target_total is not None else None,
            "shared_costs": exact(inputs.shared_costs),
            "shared_per_unit": money(shared_per_unit),
            "suggested": [
                i for i, (_, _, res) in enumerate(results) if res["decision"]["status"] in BIDDABLE
            ],
        },
    }
