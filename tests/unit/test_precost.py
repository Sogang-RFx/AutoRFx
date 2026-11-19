"""Deterministic pre-cost: unit cost, breakdown, scenarios, decision, yearly ladder, cost inputs."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from autorfx.models import (
    BreakdownInput,
    ConsistencyIssue,
    EntityMention,
    IssueEvidence,
    ParsedFile,
    PrecostInput,
    SectionInfo,
    Table,
)
from autorfx.precost import (
    PrecostInputError,
    RfqCostContext,
    breakdown_costs,
    compute,
    cost_context,
    extract_cost_inputs,
)
from autorfx.rules import COST_FIELDS, Ruleset
from contract_schema import assert_valid
from factories import make_clause, make_locus, make_manifest

D = Decimal
NO_RFQ = RfqCostContext()


def inputs(**kwargs: object) -> PrecostInput:
    base = {
        "item": "Bracket",
        "min_quantity": 1000,
        "fixed_costs": D(0),
        "variable_cost_per_unit": D(0),
        "currency": "EUR",
    }
    return PrecostInput(**{**base, **kwargs})  # type: ignore[arg-type]


BREAKDOWN = BreakdownInput(
    gross_weight=D("0.5"),
    net_weight=D("0.4"),
    material_price=D("1.0"),
    scrap_credit=D("0.2"),
    machine_hour_rate=D("180"),
    strokes_per_minute=D("40"),
    parts_per_stroke=D("1"),
    oee=D("0.75"),
    surface_cost=D("0.05"),
    packaging_cost=D("0.02"),
    freight_cost=D("0.1"),
    warehousing_cost=D("0.03"),
    scrap_rate=D("0.02"),
    sga_rate=D("0.05"),
    profit_rate=D("0.1"),
)


def issue(issue_id: str, entity: str, *values: str) -> ConsistencyIssue:
    return ConsistencyIssue(
        id=issue_id,
        category="conflict",
        severity="high",
        entity=entity,
        title=f"{entity}: 문서마다 값이 다릅니다",
        detail="",
        affects_cost=True,
        evidence=tuple(
            IssueEvidence(locus=make_locus(i + 1), text=f"value {v}", value=v, normalized=v)
            for i, v in enumerate(values)
        ),
    )


THICKNESS = issue("c-thickness", "thickness", "2.5", "2")
INCOTERM = issue("c-incoterm", "incoterm", "FCA", "DDP")
BOTH = RfqCostContext(issues=(THICKNESS, INCOTERM))


def run(
    ruleset: Ruleset,
    context: RfqCostContext = NO_RFQ,
    breakdown: BreakdownInput | None = None,
    **kwargs: object,
):
    resolved = kwargs.pop("resolved", {})
    return compute(inputs(**kwargs), breakdown=breakdown, context=context, resolved=resolved, ruleset=ruleset)  # type: ignore[arg-type]


# ── unit cost ───────────────────────────────────────────────────────────────
def test_unit_cost_matches_the_verified_case(ruleset: Ruleset):
    """COST-01, SC-005: 227 000 / 400 000 + 9.35 gives 0.5675 and 9.9175."""
    result = run(ruleset, min_quantity=400000, fixed_costs=D("227000"), variable_cost_per_unit=D("9.35"))
    assert (result["fixed_cost_per_unit"], result["unit_cost"]) == ("0.5675", "9.9175")
    assert result["inputs"]["fixed_costs"] == "227000" and result["breakdown"] is None
    assert result["range"] == {"low": "9.9175", "high": "9.9175"} and result["scenarios"] == []
    assert_valid("precost", {"data": result, "provenance": {"precost": "real"}})


def test_rounding_happens_once_at_the_end(ruleset: Ruleset):
    """COST-02: 1/3 + 0.00004 is 0.3334; rounding each part first would give 0.3333."""
    result = run(ruleset, min_quantity=3, fixed_costs=D("1"), variable_cost_per_unit=D("0.00004"))
    assert (result["fixed_cost_per_unit"], result["unit_cost"]) == ("0.3333", "0.3334")


@pytest.mark.parametrize(
    "bad",
    [
        {"min_quantity": 0},
        {"fixed_costs": D("-1")},
        {"variable_cost_per_unit": D("-0.1")},
        {"currency": "eur"},
        {"item": ""},
    ],
)
def test_invalid_input_is_an_error_not_a_result(bad: dict):
    """COST-01: a quantity of zero, a negative cost or a malformed currency never produces a number."""
    with pytest.raises(ValidationError):
        inputs(**bad)


def test_float_is_not_accepted_as_money(ruleset: Ruleset):
    """INV-5: results are decimal strings; nothing in the result is a float."""
    result = run(ruleset, breakdown=BREAKDOWN, target_price=D("1"))

    def walk(value: object) -> None:
        assert not isinstance(value, float)
        for child in value.values() if isinstance(value, dict) else value if isinstance(value, list) else ():
            walk(child)

    walk(result)


# ── breakdown ───────────────────────────────────────────────────────────────
def test_breakdown_follows_the_annex_formulas(ruleset: Ruleset):
    """COST-06: material 0.48, manufacturing 0.10, logistics 0.15, cost 0.78 × 1.02 × 1.05 = 0.83538."""
    costs = breakdown_costs(BREAKDOWN)
    assert costs["material_cost"] == D("0.48") and costs["manufacturing_cost"] == D("0.1")
    assert costs["piece_cost"] == D("0.835380") and costs["piece_price"] == D("0.9189180")
    result = run(ruleset, breakdown=BREAKDOWN)
    assert [(line["key"], line["value"]) for line in result["breakdown"]["lines"]] == [
        ("material_cost", "0.4800"),
        ("manufacturing_cost", "0.1000"),
        ("surface_cost", "0.0500"),
        ("logistics_cost", "0.1500"),
        ("overhead_cost", "0.0554"),
    ]
    assert (result["breakdown"]["piece_cost"], result["breakdown"]["piece_price"]) == ("0.8354", "0.9189")
    assert result["unit_cost"] == "0.8354", "the minimum quote is the cost without profit"
    assert result["inputs"]["variable_cost_per_unit"] == "0.8354"


def test_impossible_breakdown_is_refused():
    with pytest.raises(PrecostInputError, match="순중량"):
        breakdown_costs(BREAKDOWN.model_copy(update={"gross_weight": D("0.3")}))


# ── scenarios and the decision ──────────────────────────────────────────────
def test_unresolved_conflicts_give_a_cost_range(ruleset: Ruleset):
    """COST-07: thickness scales the weights, the delivery term decides whether freight counts."""
    result = run(ruleset, BOTH, BREAKDOWN, target_price=D("0.80"))
    assert {s["label"]: s["unit_cost"] for s in result["scenarios"]} == {
        "2.5 · FCA": "0.7283",
        "2.5 · DDP": "0.8354",
        "2 · FCA": "0.6255",
        "2 · DDP": "0.7326",
    }
    assert result["range"] == {"low": "0.6255", "high": "0.8354"}
    assert result["unit_cost"] == "0.7283", "the headline is the first value of each conflict"
    assert_valid("precost", {"data": result, "provenance": {"precost": "real"}})


@pytest.mark.parametrize(
    ("target", "status", "rule", "gap"),
    [
        (None, "baseline_undefined", "no_target", None),
        ("0.90", "go", "within_target", "0.0646"),
        ("0.60", "no_go", "above_target", "-0.2354"),
        ("0.80", "hold", "conflict_dependent", "-0.0354"),
    ],
)
def test_decision_looks_at_the_whole_range(ruleset: Ruleset, target, status: str, rule: str, gap):
    """COST-08, INV-13: go or no-go only when every case agrees; otherwise hold until resolved."""
    decision = run(ruleset, BOTH, BREAKDOWN, target_price=D(target) if target else None)["decision"]
    assert (decision["status"], decision["rule"], decision["gap"]) == (status, rule, gap)
    assert decision["note"] and len([r for r in decision["reasons"] if "해소되지 않은 충돌" in r]) == 2


def test_resolving_a_conflict_narrows_the_range_and_can_settle_the_decision(ruleset: Ruleset):
    """COST-08: with both conflicts resolved there is one figure and a firm decision."""
    one = run(ruleset, BOTH, BREAKDOWN, target_price=D("0.80"), resolved={"c-thickness": "2"})
    assert [s["label"] for s in one["scenarios"]] == ["FCA", "DDP"]
    assert one["range"] == {"low": "0.6255", "high": "0.7326"} and one["decision"]["status"] == "go"
    both = run(
        ruleset, BOTH, BREAKDOWN, target_price=D("0.80"), resolved={"c-thickness": "2.5", "c-incoterm": "DDP"}
    )
    assert both["scenarios"] == [] and both["unit_cost"] == "0.8354"
    assert (both["decision"]["status"], both["decision"]["rule"]) == ("no_go", "above_target")
    with pytest.raises(PrecostInputError, match="고를 수 있는 값"):
        run(ruleset, BOTH, BREAKDOWN, resolved={"c-thickness": "3"})


@pytest.mark.parametrize("entity", ["target_price", "total_volume", "currency", "net_weight"])
def test_a_conflict_no_driver_can_evaluate_holds_the_decision(ruleset: Ruleset, entity: str):
    """INV-13 (review HIGH): a cost conflict without a driver cannot be evaluated per value, so it is
    not skipped: the decision holds until a value is chosen."""
    conflict = issue(f"c-{entity}", entity, "1.50", "0.90")
    context = RfqCostContext(issues=(conflict,))
    held = run(ruleset, context, BREAKDOWN, target_price=D("1.00"))
    assert (held["decision"]["status"], held["decision"]["rule"]) == ("hold", "missing_inputs")
    assert any(entity in reason for reason in held["decision"]["reasons"])
    settled = run(ruleset, context, BREAKDOWN, target_price=D("1.00"), resolved={f"c-{entity}": "0.90"})
    assert settled["decision"]["status"] == "go", "choosing a value closes the conflict"


def test_resolving_something_that_is_not_an_open_cost_conflict_is_refused(ruleset: Ruleset):
    """COST-08 (review HIGH): an unknown id is an error, not silently ignored."""
    with pytest.raises(PrecostInputError, match="원가 충돌이 아닙니다: bogus"):
        run(ruleset, BOTH, BREAKDOWN, target_price=D("1.00"), resolved={"bogus": "9"})


def test_conflict_without_a_breakdown_holds_the_decision(ruleset: Ruleset):
    """INV-13: without the breakdown the effect of a conflict is unknown, so the decision waits."""
    result = run(ruleset, BOTH, variable_cost_per_unit=D("0.5"), target_price=D("0.80"))
    assert (result["decision"]["status"], result["decision"]["rule"]) == ("hold", "missing_inputs")
    assert any("원가분해" in reason for reason in result["decision"]["reasons"])
    assert result["scenarios"] == []


def test_margin_comes_from_the_rule_file(ruleset: Ruleset):
    """COST-08, principle 8: a required margin of 10 % turns a go at 0.90 into a hold."""
    strict = ruleset.model_copy(
        update={"cost_model": ruleset.cost_model.model_copy(update={"margin": "0.1"})}
    )
    assert run(strict, BOTH, BREAKDOWN, target_price=D("0.90"))["decision"]["status"] == "hold"
    assert run(ruleset, BOTH, BREAKDOWN, target_price=D("0.90"))["decision"]["status"] == "go"


def test_yearly_ladder_applies_the_reduction_from_the_second_year(ruleset: Ruleset):
    """COST-09: 1.20 less 3 % a year from year two is 1.2000, 1.1640, 1.1291; the first loss year is named."""
    context = RfqCostContext(
        annual_reduction_rate=D("0.03"), reduction_starts_at_index=1, years=(2028, 2029, 2030)
    )
    result = run(ruleset, context, variable_cost_per_unit=D("1.15"), target_price=D("1.20"))
    assert [(y["year"], y["price"], y["below_cost"]) for y in result["yearly"]] == [
        (2028, "1.2000", False),
        (2029, "1.1640", False),
        (2030, "1.1291", True),
    ]
    assert result["decision"]["status"] == "go"
    assert any("2030년부터" in reason for reason in result["decision"]["reasons"])


# ── cost inputs from the RFQ ────────────────────────────────────────────────
def package():
    price = make_clause("4.2", ["4.2 The target price is 1.20 EUR per piece."])
    spec = make_clause("3.1", ["3.1 Sheet thickness 2.5 mm."], source_doc="d02")
    reduction = make_clause(
        "4.3", ["4.3 Annual price reduction of 3 % from the 2nd delivery year."], first_sentence=2
    )
    grid_locus = make_locus(2, source_doc="d03", section_id="Volumes", anchor="Volumes!A2:D2")
    grid = Table(
        source_doc="d03",
        page_number=1,
        section_id="Volumes",
        title="Volumes",
        header=("Year", "2028", "2029", "2030"),
        rows=(("AB-100 LH [k pcs]", "60", "120", "140"),),
        row_loci=(grid_locus,),
        cell_anchors=(("Volumes!A2", "Volumes!B2", "Volumes!C2", "Volumes!D2"),),
    )
    sheet = make_clause(
        "Volumes",
        ["Year | 2028 | 2029 | 2030", "AB-100 LH [k pcs] | 60 | 120 | 140", "Volumes are planning figures."],
        source_doc="d03",
        kind="table",
    )
    clauses = [price, reduction, spec, sheet]
    mentions = [
        EntityMention(
            entity="target_price",
            raw="1.20 EUR",
            normalized="1.2",
            locus=price.sentences[0].locus,
            text=price.text,
        ),
        EntityMention(
            entity="annual_reduction_rate",
            raw="3 %",
            normalized="3",
            unit="%",
            locus=reduction.sentences[0].locus,
            text=reduction.text,
        ),
        EntityMention(
            entity="thickness", raw="2.5 mm", normalized="2.5", locus=spec.sentences[0].locus, text=spec.text
        ),
        EntityMention(
            entity="thickness",
            raw="2",
            normalized="2",
            scope="AB-100",
            locus=sheet.sentences[1].locus,
            text="row",
        ),
    ]
    thickness = THICKNESS.model_copy(
        update={
            "cost_fields": ("gross_weight", "net_weight"),
            "evidence": (
                IssueEvidence(
                    locus=spec.sentences[0].locus, text=spec.text, value="2.5 mm", normalized="2.5"
                ),
                IssueEvidence(locus=sheet.sentences[1].locus, text="row", value="2", normalized="2"),
            ),
        }
    )
    parsed = [
        ParsedFile(
            file_id="d03",
            lang="en",
            page_count=1,
            sentences=sheet.sentences,
            sections=(SectionInfo(section_id="Volumes", kind="table"),),
            tables=(grid,),
        )
    ]
    return parsed, clauses, mentions, [thickness]


def test_cost_inputs_are_cited_and_never_invented(ruleset: Ruleset):
    """COST-05: RFQ values are cited; conflicts list alternatives; examples are labelled; rest is missing."""
    parsed, clauses, mentions, issues = package()
    data = extract_cost_inputs(make_manifest(), parsed, clauses, mentions, issues, ruleset)
    assert_valid("cost-inputs", {"data": data, "provenance": {"cost_inputs": "real"}})
    assert [c["field"] for c in data["candidates"]] == list(COST_FIELDS)
    by = {c["field"]: c for c in data["candidates"]}
    assert data["item"] == "AB-100"

    target = by["target_price"]
    assert (target["value"], target["source"]) == ("1.2", "rfq")
    assert target["evidence"]["citation_status"] == "verified"
    assert target["evidence"]["finding"]["citations"][0]["section_id"] == "4.2"

    thickness = by["thickness"]
    assert thickness["value"] is None and thickness["issue_id"] == "c-thickness"
    assert [a["value"] for a in thickness["alternatives"]] == ["2.5", "2"]
    assert all(a["evidence"]["citation_status"] == "verified" for a in thickness["alternatives"])
    assert by["net_weight"]["issue_id"] == by["gross_weight"]["issue_id"] == "c-thickness", (
        "blocked by the conflict"
    )

    volume = by["min_quantity"]
    assert (volume["value"], volume["source"]) == ("320000", "rfq")
    assert (
        "구매 확약이 아니" in volume["note"]
        and volume["evidence"]["finding"]["citations"][0]["anchor"] == "Volumes!A2:D2"
    )

    assert (by["material_price"]["value"], by["material_price"]["source"]) == ("1.05", "assumption")
    assert "예시" in by["material_price"]["note"]
    assert (by["surface_cost"]["value"], by["surface_cost"]["source"], by["surface_cost"]["evidence"]) == (
        None,
        "missing",
        None,
    )


@pytest.mark.parametrize(
    ("text", "rate", "starts"),
    [
        ("Jährliche Preisreduzierung von 3 % ab dem 2. Jahr.", "3", 1),
        ("Jährliche Preisreduzierung von 3 % ab dem zweiten Lieferjahr.", "3", 1),
        ("Annual price reduction of 2.5 % from the first year.", "2.5", 0),
        ("Annual price reduction of 3 % from the 2nd delivery year.", "3", 1),
    ],
)
def test_the_year_the_reduction_starts_is_read_from_the_sentence(text: str, rate: str, starts: int):
    """COST-09 (review MEDIUM): "2. Jahr" is the second year; "2.5 %" is a rate, not an ordinal."""
    mention = EntityMention(
        entity="annual_reduction_rate",
        raw=f"{rate} %",
        normalized=rate,
        unit="%",
        locus=make_locus(1),
        text=text,
    )
    context = cost_context([mention], [], [])
    assert (context.annual_reduction_rate, context.reduction_starts_at_index) == (D(rate) / 100, starts)


def test_cost_context_reads_the_reduction_and_the_delivery_years(ruleset: Ruleset):
    """COST-09: 3 % from the second delivery year, over the years of the volume table."""
    parsed, _, mentions, issues = package()
    context = cost_context(mentions, issues, parsed)
    assert (context.annual_reduction_rate, context.reduction_starts_at_index) == (D("0.03"), 1)
    assert context.years == (2028, 2029, 2030) and [i.id for i in context.issues] == ["c-thickness"]
