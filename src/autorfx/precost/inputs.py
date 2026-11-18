"""Cost input candidates found in the RFQ (COST-05).

A value the RFQ states is offered with its citation and is not used until the
user confirms it. When files disagree, the candidate has no value: it lists
the alternatives, each with its own citation, and names the consistency issue
that must be resolved. A supplier assumption is never presented as an RFQ
fact: the only assumptions offered are the examples in ``cost_model.yaml``,
labelled as examples. Nothing is invented.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from autorfx.analysis.citation import verify
from autorfx.consistency.normalize import multiplier_of, parse_number
from autorfx.models import (
    Clause,
    ConsistencyIssue,
    EntityMention,
    Finding,
    Locus,
    PackageManifest,
    ParsedFile,
    Table,
    VerifiedFinding,
)
from autorfx.precost.calc import RfqCostContext
from autorfx.rules import COST_FIELDS, Ruleset

FIELDS: dict[str, tuple[str, str | None]] = {
    "currency": ("통화", None),
    "min_quantity": ("총 계획 물량", "개"),
    "fixed_costs": ("고정비 (금형·개발비)", None),
    "variable_cost_per_unit": ("단위당 변동비", None),
    "target_price": ("목표가", None),
    "annual_reduction_rate": ("연간 단가 인하율", "%"),
    "gross_weight": ("투입 중량", "kg"),
    "net_weight": ("순중량", "kg"),
    "material_price": ("소재 단가", "/kg"),
    "scrap_credit": ("스크랩 환입 단가", "/kg"),
    "machine_hour_rate": ("설비 시간당 비용", "/h"),
    "strokes_per_minute": ("분당 타수", "spm"),
    "parts_per_stroke": ("타당 생산 수", "개"),
    "oee": ("설비종합효율(OEE)", None),
    "surface_cost": ("표면처리비", "/개"),
    "packaging_cost": ("포장비", "/개"),
    "freight_cost": ("운임", "/개"),
    "warehousing_cost": ("창고·안전재고비", "/개"),
    "scrap_rate": ("불량·재작업률", None),
    "sga_rate": ("판관비율", None),
    "profit_rate": ("이익률", None),
    "thickness": ("판재 두께", "mm"),
    "incoterm": ("인도조건", None),
    "payment_days": ("결제 기한", "일"),
}
YEAR = re.compile(r"^20\d{2}$")
VOLUME_ROW = re.compile(r"pcs|pieces|stück|stk", re.IGNORECASE)
NOT_BINDING = re.compile(
    r"purchase commitment|Abnahmeverpflichtung|planning figures|Planwerte", re.IGNORECASE
)
# "from the 2nd/second year", "ab dem zweiten / 2. Jahr". The ordinal "2." must stand alone:
# "2.5 % from the first year" is a rate, not the second year.
FROM_SECOND = re.compile(r"\b(?:2nd|second|zweite[nmr]?)\b|(?<![\d.,])2\.(?=\s)", re.IGNORECASE)


def _evidence(
    label: str, raw: str, locus: Locus, department: str, clauses: Sequence[Clause]
) -> VerifiedFinding:
    finding = Finding(
        kind="cost_input", departments=(department,), text=f"{label}: {raw}", citations=(locus,)
    )
    return verify(finding, clauses)


def _volume(tables: Sequence[Table]) -> tuple[int, tuple[int, ...], Locus, str] | None:
    """Total planned volume of the first part row of a year grid, the years, and the row cited."""
    for table in tables:
        # The row of years is the table's header, or a row below a caption line.
        years_row: tuple[str, ...] = table.header
        columns = [i for i, cell in enumerate(years_row) if YEAR.match(cell.strip())]
        for row, locus in zip(table.rows, table.row_loci, strict=True):
            in_row = [i for i, cell in enumerate(row) if YEAR.match(cell.strip())]
            if len(in_row) >= 2:
                years_row, columns = row, in_row
                continue
            if len(columns) < 2 or not row or not VOLUME_ROW.search(row[0]):
                continue
            values = [parse_number(row[i], "en") for i in columns if i < len(row)]
            if not values or any(v is None for v in values):
                continue
            total = sum(v for v in values if v is not None) * multiplier_of(row[0])
            years = tuple(int(years_row[i]) for i in columns)
            return int(total), years, locus, " | ".join(c for c in row if c)
    return None


def cost_context(
    mentions: Sequence[EntityMention],
    issues: Sequence[ConsistencyIssue],
    parsed: Sequence[ParsedFile],
) -> RfqCostContext:
    """Annual reduction, delivery years and the cost-related issues of an RFQ."""
    rate: Decimal | None = None
    starts = 1
    for mention in mentions:
        if mention.entity == "annual_reduction_rate":
            try:
                rate = Decimal(mention.normalized) / Decimal(100)
            except InvalidOperation:
                continue
            starts = 1 if FROM_SECOND.search(mention.text) else 0
            break
    volume = _volume([t for file in parsed for t in file.tables])
    return RfqCostContext(
        issues=tuple(i for i in issues if i.affects_cost),
        annual_reduction_rate=rate,
        reduction_starts_at_index=starts,
        years=volume[1] if volume else (),
    )


def extract_cost_inputs(
    manifest: PackageManifest,
    parsed: Sequence[ParsedFile],
    clauses: Sequence[Clause],
    mentions: Sequence[EntityMention],
    issues: Sequence[ConsistencyIssue],
    ruleset: Ruleset,
) -> dict:
    """The ``data`` object of ``contract/cost-inputs.schema.json``: one candidate per cost field."""
    entities = {rule.id: rule for rule in ruleset.entities}
    # Only a cost-affecting conflict can be resolved in a calculation; others are not offered.
    conflicts = {i.entity: i for i in issues if i.category == "conflict" and i.entity and i.affects_cost}
    blocking = {field: i.id for i in issues if i.affects_cost for field in i.cost_fields}
    examples = ruleset.cost_model.example_assumptions
    volume = _volume([t for file in parsed for t in file.tables])
    not_binding = any(NOT_BINDING.search(s.text) for file in parsed for s in file.sentences)
    part = next((m.scope for m in mentions if m.scope), None)

    candidates = []
    for field in COST_FIELDS:
        label, unit = FIELDS[field]
        found = [m for m in mentions if m.entity == field]
        base = {
            "field": field,
            "label": label,
            "unit": unit,
            "value": None,
            "source": "missing",
            "note": "RFQ에 적혀 있지 않습니다.",
            "evidence": None,
            "alternatives": [],
            "issue_id": blocking.get(field),
        }
        rule = entities.get(field)
        conflict = conflicts.get(field)
        if found and rule and conflict:
            seen: dict[str, EntityMention] = {}
            for mention in found:
                seen.setdefault(mention.normalized, mention)
            base |= {
                "source": "rfq",
                "note": "파일마다 값이 다릅니다. 정합성 이슈를 해소한 뒤 값을 고르세요.",
                "issue_id": conflict.id,
                "alternatives": [
                    {
                        "value": value,
                        "evidence": _evidence(label, m.raw, m.locus, rule.department, clauses).model_dump(
                            mode="json"
                        ),
                    }
                    for value, m in seen.items()
                ],
            }
        elif found and rule:
            first = found[0]
            base |= {
                "value": first.normalized,
                "source": "rfq",
                "note": "RFQ에 적힌 값입니다. 확인한 뒤 사용하세요.",
                "evidence": _evidence(label, first.raw, first.locus, rule.department, clauses).model_dump(
                    mode="json"
                ),
            }
        elif field == "min_quantity" and volume:
            total, _, locus, text = volume
            note = "연도별 계획 물량의 합입니다."
            if not_binding:
                note += " RFQ는 이 물량이 구매 확약이 아니라고 밝히고 있습니다."
            base |= {
                "value": str(total),
                "source": "rfq",
                "note": note,
                "evidence": _evidence(label, text, locus, "sales", clauses).model_dump(mode="json"),
            }
        elif field in examples:
            base |= {
                "value": examples[field],
                "source": "assumption",
                "note": "RFQ에 없는 값입니다. 예시 가정이므로 자사 값으로 바꿔야 합니다.",
            }
        candidates.append(base)
    return {"item": part or manifest.name, "candidates": candidates}
