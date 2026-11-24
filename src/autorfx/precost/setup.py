"""What one RFQ needs before a pre-cost (steps ①–⑤).

① The RFQ type, from rule patterns. ③ The qualifications and contract terms
the RFQ requires, each checked against the company profile and the answers
the company already gave; what cannot be settled becomes a yes/no/unknown
question. ⑦ Clauses that pass a shock to the customer (raw-material
indexation, exchange-rate adjustment). ⑤ Cost items suggested from the
company's price table. Every requirement and clause is cited.

All of this is rule-based. A connected LLM may later classify or phrase, but
the comparison with the profile and every number stay in code.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal

from autorfx.analysis.citation import verify
from autorfx.consistency.normalize import parse_number
from autorfx.models import Clause, EntityMention, Finding, Locus, ParsedSentence
from autorfx.precost.company import Company, canonical
from autorfx.precost.lines import MAX_LINES
from autorfx.rules import RequirementRule, Ruleset

MIO = re.compile(r"\b(Mio\.?|million|Millionen|M)\b", re.IGNORECASE)


def _sentences(clauses: Sequence[Clause]) -> Iterable[ParsedSentence]:
    for clause in clauses:
        yield from clause.sentences


def _cite(kind: str, text: str, locus: Locus, department: str, clauses: Sequence[Clause]) -> dict:
    finding = Finding(kind=kind, departments=(department,), text=text, citations=(locus,))
    return verify(finding, clauses).model_dump(mode="json")


def classify(clauses: Sequence[Clause], ruleset: Ruleset) -> dict:
    """① The RFQ type: the type with the most distinct patterns found, or "other"."""
    rules = ruleset.cost_model.rfq_types
    texts = [s.text for s in _sentences(clauses)]
    hits: dict[str, list[str]] = {}
    for rfq_type in rules.types:
        found = []
        for pattern in rfq_type.patterns.all():
            regex = re.compile(pattern, re.IGNORECASE)
            if any(regex.search(t) for t in texts):
                found.append(pattern)
        hits[rfq_type.id] = found
    best = max(rules.types, key=lambda t: len(hits[t.id]))
    signals = {t.id: len(hits[t.id]) for t in rules.types}
    if len(hits[best.id]) < rules.min_hits:
        return {
            "type": "other",
            "label": "기타",
            "decision_unit": "rfq",
            "baseline": "none",
            "automatic": False,
            "signals": signals,
            "note": "부품 제조나 유통으로 볼 근거가 부족합니다. "
            "자동 판정 대상이 아니므로 수기로 원가를 검토하세요.",
        }
    note = {
        "manufacturing": "부품 제조 RFQ입니다. 목표가와 비교해 판정합니다.",
        "distribution": "유통·재판매 RFQ입니다. 목표가가 없어 판정 대신 최소 입찰가를 냅니다. "
        "유통 산식으로 계산합니다: 매입가 + 운영비(인건비·물류 등)에 마진만 붙이고 "
        "불량·판관비 가산은 하지 않습니다. 품목별 입찰은 ④에서 고릅니다.",
    }.get(best.id, f"{best.label} RFQ입니다.")
    if not best.automatic:
        note = f"{best.label} RFQ입니다. 자동 판정 대상이 아니므로 수기로 원가를 검토하세요."
    return {
        "type": best.id,
        "label": best.label,
        "decision_unit": best.decision_unit,
        "baseline": best.baseline,
        "automatic": best.automatic,
        "signals": signals,
        "note": note,
    }


def _amount(text: str) -> Decimal | None:
    number = parse_number(re.sub(r"[^\d.,]", " ", text).strip().split(" ")[0], "de" if "," in text else "en")
    if number is None:
        return None
    return number * Decimal(1_000_000) if MIO.search(text) else number


def _found(rule: RequirementRule, clauses: Sequence[Clause]) -> list[tuple[str, ParsedSentence]]:
    """(value, sentence) for each distinct value the rule finds, first sentence first."""
    seen: dict[str, tuple[str, ParsedSentence]] = {}
    for sentence in _sentences(clauses):
        if rule.named_patterns:
            for name, patterns in rule.named_patterns.items():
                if name not in seen and any(re.search(p, sentence.text, re.IGNORECASE) for p in patterns):
                    seen[name] = (name, sentence)
            continue
        for pattern in rule.patterns.all():
            for match in re.finditer(pattern, sentence.text, re.IGNORECASE):
                value = " ".join((match.groupdict().get("value") or match.group(0)).split())
                seen.setdefault(canonical(value), (value, sentence))
    return list(seen.values())


def requirements(
    clauses: Sequence[Clause], mentions: Sequence[EntityMention], company: Company, ruleset: Ruleset
) -> list[dict]:
    """③ Every requirement of the RFQ with what is already known about it.

    ``status`` is "pass" or "fail" when the profile or a stored answer settles it, and
    "ask" when the user has to answer. The calculation applies the answers.
    """
    checks: list[dict] = []
    settings = company.settings

    def add(
        rule: RequirementRule, key: str, value: str, sentence: ParsedSentence | None, **extra: str
    ) -> dict:
        check = {
            "key": key,
            "requirement": rule.id,
            "label": rule.label,
            "department": rule.department,
            "kind": rule.kind,
            "value": value,
            "question": rule.question.format(value=value, **extra),
            "status": "ask",
            "source": None,
            "evidence": _cite(
                "cost_input", f"{rule.label}: {sentence.text}", sentence.locus, rule.department, clauses
            )
            if sentence
            else None,
        }
        checks.append(check)
        return check

    for rule in ruleset.cost_model.requirements:
        if rule.id == "payment":
            days = [m for m in mentions if m.entity == "payment_days"]
            numbers = [(Decimal(m.normalized), m) for m in days if _is_number(m.normalized)]
            if not numbers:
                continue
            value, mention = max(numbers, key=lambda n: n[0])
            limit = settings.max_payment_days
            sentence = next((s for s in _sentences(clauses) if s.locus.key() == mention.locus.key()), None)
            check = add(
                rule,
                f"payment:{value}",
                format(value.normalize(), "f"),
                sentence,
                limit=f"{limit}일" if limit is not None else "미입력",
            )
            if limit is not None and value <= limit:
                check |= {"status": "pass", "source": "profile"}
            continue
        for value, sentence in _found(rule, clauses):
            key = f"{rule.id}:{canonical(value)}"
            if rule.id == "liability_cap":
                amount = _amount(value)
                limit = settings.max_liability
                check = add(
                    rule, key, value, sentence, limit=format(limit, "f") if limit is not None else "미입력"
                )
                if amount is not None and limit is not None and amount <= limit:
                    check |= {"status": "pass", "source": "profile"}
                continue
            check = add(rule, key, value, sentence)
            if rule.kind != "fact":
                continue
            if rule.profile_field and company.profile.has(rule.profile_field, value):
                check |= {"status": "pass", "source": "profile"}
            elif (stored := company.answer(key)) is not None and stored.answer != "unknown":
                check |= {"status": "pass" if stored.answer == "yes" else "fail", "source": "stored"}
    return checks


def _is_number(text: str) -> bool:
    try:
        Decimal(text)
    except ArithmeticError:
        return False
    return True


def pass_through(clauses: Sequence[Clause], ruleset: Ruleset) -> dict[str, dict | None]:
    """⑦ Clauses that pass a shock to the customer: the first sentence found, cited."""
    found: dict[str, dict | None] = {}
    for rule_id, rule in ruleset.cost_model.pass_through.items():
        found[rule_id] = None
        regexes = [re.compile(p, re.IGNORECASE) for p in rule.patterns.all()]
        for sentence in _sentences(clauses):
            if any(r.search(sentence.text) for r in regexes):
                found[rule_id] = {
                    "label": rule.label,
                    "evidence": _cite(
                        "cost_input", f"{rule.label}: {sentence.text}", sentence.locus, "costing", clauses
                    ),
                }
                break
    return found


EITHER_LABEL = "둘 다 가능 (발주처가 선택)"


def award_mode(clauses: Sequence[Clause], ruleset: Ruleset) -> dict:
    """How the RFQ awards: per line item, the whole RFQ only, either, or not stated (cited when found).

    When one sentence names both, "all or none" wins: it is the restrictive one, and a split
    pattern such as "award by lot" can appear in a sentence that rules it out. When separate
    sentences name each, the customer chooses between them after the bids (``either``): every
    line must then stand on its own, as under a split award.
    """
    rules = ruleset.cost_model.award
    sentences = list(_sentences(clauses))
    first: dict[str, int] = {}
    for mode in ("all_or_none", "split"):
        rule = rules.get(mode)
        if rule is None:
            continue
        regexes = [re.compile(p, re.IGNORECASE) for p in rule.patterns.all()]
        at = next((i for i, s in enumerate(sentences) if any(r.search(s.text) for r in regexes)), None)
        if at is not None:
            first[mode] = at
    whole, split = first.get("all_or_none"), first.get("split")
    if whole is not None and split is not None and whole != split:
        sentence = sentences[min(whole, split)]
        return {
            "mode": "either",
            "label": EITHER_LABEL,
            "evidence": _cite(
                "cost_input", f"{EITHER_LABEL}: {sentence.text}", sentence.locus, "sales", clauses
            ),
        }
    for mode, at in (("all_or_none", whole), ("split", split)):
        if at is not None:
            label = rules[mode].label
            sentence = sentences[at]
            return {
                "mode": mode,
                "label": label,
                "evidence": _cite(
                    "cost_input", f"{label}: {sentence.text}", sentence.locus, "sales", clauses
                ),
            }
    return {"mode": None, "label": "낙찰 방식 명시 없음", "evidence": None}


def suggested_items(cost_inputs: dict, company: Company, today: date) -> list[dict]:
    """⑤ Cost items the price table can already price for this RFQ (material named in the RFQ).

    Only rows whose name contains the material the RFQ states are suggested; the quantity
    is the RFQ's net weight when it states one. Everything else the user adds from the table.
    """
    values = {c["field"]: c for c in cost_inputs.get("candidates", ())}
    material = (values.get("material") or {}).get("value")
    weight = (values.get("net_weight") or {}).get("value")
    if not material:
        return []
    wanted = canonical(material)
    items = []
    for row in company.prices:
        if (
            row.category == "material"
            and wanted
            and (wanted in canonical(row.name) or canonical(row.name) in wanted)
        ):
            items.append(
                {
                    "label": row.name,
                    "category": "material",
                    "quantity": weight or "0",
                    "unit": row.unit,
                    "unit_price": format(row.price, "f"),
                    "price_id": row.id,
                    "expired": row.valid_until < today,
                    "note": "RFQ의 소재와 이름이 맞는 단가표 행입니다. 수량은 투입 중량으로 고치세요."
                    if weight
                    else "RFQ의 소재와 이름이 맞는 단가표 행입니다. 수량(투입 중량)을 넣으세요.",
                }
            )
    return items


def prepare(
    *,
    cost_inputs: dict,
    clauses: Sequence[Clause],
    mentions: Sequence[EntityMention],
    company: Company,
    ruleset: Ruleset,
    today: date,
    lines: dict | None = None,
) -> dict:
    """The ``data`` object of ``contract/precost-setup.schema.json``."""
    return {
        "classification": classify(clauses, ruleset),
        "award": award_mode(clauses, ruleset),
        "requirements": requirements(clauses, mentions, company, ruleset),
        "pass_through": [
            {
                "id": key,
                "label": ruleset.cost_model.pass_through[key].label,
                "evidence": value["evidence"] if value else None,
            }
            for key, value in pass_through(clauses, ruleset).items()
        ],
        "cost_inputs": cost_inputs,
        "suggested_items": suggested_items(cost_inputs, company, today),
        "assumption_note": ruleset.cost_model.assumption_note,
        "lines": lines or {"items": [], "total": 0, "limit": MAX_LINES, "tables": []},
    }


__all__ = [
    "EITHER_LABEL",
    "award_mode",
    "classify",
    "pass_through",
    "prepare",
    "requirements",
    "suggested_items",
]
