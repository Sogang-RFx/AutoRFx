"""Cost inputs the RFQ itself states (COST-05, step ④).

A value the RFQ states is offered with its citation and is not used until the
user confirms it. When files disagree, the candidate has no value: it lists
the alternatives, each with its own citation, and names the consistency issue
that must be resolved. The supplier's own figures (unit prices, rates) are not
here: they come from the company settings (:mod:`autorfx.precost.company`).
Nothing is invented.
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
from autorfx.rules import COST_FIELDS, Ruleset

FIELDS: dict[str, tuple[str, str | None]] = {
    "currency": ("통화", None),
    "volume": ("총 계획 물량", "개"),
    "target_price": ("목표가", None),
    "annual_reduction_rate": ("연간 단가 인하율", "%"),
    "payment_days": ("결제 기한", "일"),
    "incoterm": ("인도조건", None),
    "thickness": ("판재 두께", "mm"),
    "net_weight": ("순중량", "kg"),
    "material": ("소재", None),
}
ENTITY_OF = {"volume": "total_volume"}
YEAR = re.compile(r"^20\d{2}$")
VOLUME_ROW = re.compile(r"pcs|pieces|stück|stk|units|대|개", re.IGNORECASE)
NOT_BINDING = re.compile(
    r"purchase commitment|Abnahmeverpflichtung|planning figures|Planwerte|not (a )?binding", re.IGNORECASE
)
# "from the 2nd/second year", "ab dem zweiten / 2. Jahr". The ordinal "2." must stand alone:
# "2.5 % from the first year" is a rate, not the second year.
FROM_SECOND = re.compile(r"\b(?:2nd|second|zweite[nmr]?)\b|(?<![\d.,])2\.(?=\s)", re.IGNORECASE)


def evidence(
    label: str, raw: str, locus: Locus, department: str, clauses: Sequence[Clause]
) -> VerifiedFinding:
    finding = Finding(
        kind="cost_input", departments=(department,), text=f"{label}: {raw}", citations=(locus,)
    )
    return verify(finding, clauses)


def yearly_volumes(tables: Sequence[Table]) -> tuple[tuple[tuple[int, int], ...], Locus, str] | None:
    """Planned quantity per year of the first part row of a year grid, and the row cited."""
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
            values = [parse_number(row[i], "en") if i < len(row) else None for i in columns]
            if not values or any(v is None for v in values):
                continue
            factor = multiplier_of(row[0])
            per_year = tuple(
                (int(years_row[i]), int(v * factor))
                for i, v in zip(columns, values, strict=True)
                if v is not None
            )
            return per_year, locus, " | ".join(c for c in row if c)
    return None


def reduction(mentions: Sequence[EntityMention]) -> tuple[Decimal | None, int]:
    """Annual reduction as a fraction, and the index of the first reduced year."""
    for mention in mentions:
        if mention.entity == "annual_reduction_rate":
            try:
                rate = Decimal(mention.normalized) / Decimal(100)
            except InvalidOperation:
                continue
            return rate, 1 if FROM_SECOND.search(mention.text) else 0
    return None, 1


def extract_cost_inputs(
    manifest: PackageManifest,
    parsed: Sequence[ParsedFile],
    clauses: Sequence[Clause],
    mentions: Sequence[EntityMention],
    issues: Sequence[ConsistencyIssue],
    ruleset: Ruleset,
) -> dict:
    """The ``data`` object of ``contract/cost-inputs.schema.json``: one candidate per RFQ cost field."""
    entities = {rule.id: rule for rule in ruleset.entities}
    # Only a cost-affecting conflict can be resolved in a calculation; others are not offered.
    conflicts = {i.entity: i for i in issues if i.category == "conflict" and i.entity and i.affects_cost}
    blocking = {field: i.id for i in issues if i.affects_cost for field in i.cost_fields}
    volume = yearly_volumes([t for file in parsed for t in file.tables])
    not_binding = any(NOT_BINDING.search(s.text) for file in parsed for s in file.sentences)
    part = next((m.scope for m in mentions if m.scope), None)
    rate, starts = reduction(mentions)

    candidates = []
    for field in COST_FIELDS:
        label, unit = FIELDS[field]
        entity = ENTITY_OF.get(field, field)
        found = [m for m in mentions if m.entity == entity]
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
        rule = entities.get(entity)
        conflict = conflicts.get(entity)
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
                        "evidence": evidence(label, m.raw, m.locus, rule.department, clauses).model_dump(
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
                "evidence": evidence(label, first.raw, first.locus, rule.department, clauses).model_dump(
                    mode="json"
                ),
            }
        elif field == "volume" and volume:
            per_year, locus, text = volume
            note = "연도별 계획 물량의 합입니다."
            if not_binding:
                note += " RFQ는 이 물량이 구매 확약이 아니라고 밝히고 있습니다."
            base |= {
                "value": str(sum(q for _, q in per_year)),
                "source": "rfq",
                "note": note,
                "evidence": evidence(label, text, locus, "sales", clauses).model_dump(mode="json"),
            }
        candidates.append(base)
    return {
        "item": part or manifest.name,
        "candidates": candidates,
        "volumes": [{"year": y, "quantity": q} for y, q in (volume[0] if volume else ())],
        "reduction_starts_at_index": starts,
        "annual_reduction_rate": format(rate, "f") if rate is not None else None,
    }
