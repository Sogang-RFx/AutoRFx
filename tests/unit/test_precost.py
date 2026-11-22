"""Pre-cost and Go/No-Go: cost items, lifetime minimum quote, shocks, NPV, knockouts, score, setup."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from autorfx.models import (
    ConsistencyIssue,
    CostItem,
    EntityMention,
    IssueEvidence,
    ParsedFile,
    PrecostInput,
    SectionInfo,
    Table,
)
from autorfx.precost import (
    Company,
    CompanyError,
    PrecostInputError,
    RfqCostContext,
    StoredAnswer,
    classify,
    evaluate,
    extract_cost_inputs,
    pass_through,
    requirements,
)
from autorfx.precost.calc import cash_flows, lifetime_factor, npv, payback, piece_costs
from autorfx.precost.company import CompanyStore, PriceRow, defaults, validate
from autorfx.precost.inputs import reduction
from autorfx.precost.setup import suggested_items
from autorfx.rules import COST_FIELDS, Ruleset
from contract_schema import assert_valid
from factories import make_clause, make_locus, make_manifest

D = Decimal
TODAY = date(2026, 11, 22)
NO_RFQ = RfqCostContext()
ITEMS = (
    CostItem(label="강판 HC420LA", category="material", quantity=D("2"), unit="kg", unit_price=D("1.05")),
    CostItem(label="프레스 400톤", category="process", quantity=D("0.004"), unit="h", unit_price=D("180")),
    CostItem(label="스크랩 환입", category="credit", quantity=D("0.3"), unit="kg", unit_price=D("0.2")),
    CostItem(label="DDP 운임", category="freight", quantity=D("1"), unit="개", unit_price=D("0.05")),
)
UPLIFT = D("1.015") * D("1.08")


@pytest.fixture
def company(ruleset: Ruleset) -> Company:
    return defaults(ruleset)


def inputs(**change: object) -> PrecostInput:
    base: dict = {
        "item": "Bracket",
        "currency": "EUR",
        "target_price": D("4"),
        "volumes": [
            {"year": 2028, "quantity": 100_000},
            {"year": 2029, "quantity": 100_000},
            {"year": 2030, "quantity": 100_000},
        ],
        "annual_reduction_rate": D("0.03"),
        "reduction_starts_at_index": 1,
        "payment_days": 60,
        "items": ITEMS,
    }
    return PrecostInput.model_validate({**base, **change})


def run(
    ruleset: Ruleset, company: Company, context: RfqCostContext = NO_RFQ, resolved=None, **change: object
) -> dict:
    result = evaluate(
        inputs(**change),
        context=context,
        company=company,
        resolved=resolved or {},
        ruleset=ruleset,
        today=TODAY,
    )
    assert_valid("precost", {"data": result, "provenance": {"precost": "real"}})
    return result


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
MANUFACTURING = {
    "type": "manufacturing",
    "label": "부품 제조",
    "decision_unit": "rfq",
    "baseline": "target_price",
    "automatic": True,
    "signals": {},
    "note": "",
}


# ── cost of one piece ───────────────────────────────────────────────────────
def test_every_line_is_quantity_times_unit_price(company: Company):
    """Generic cost: material 2.10 + process 0.72 + freight 0.05 − credit 0.06, then scrap and SG&A."""
    piece = piece_costs(ITEMS, company)
    assert piece["direct"] == D("2.81")
    assert piece["piece_cost"] == D("2.81") * UPLIFT
    assert piece["material_part"] == (D("2.10") - D("0.06")) * UPLIFT


def test_a_credit_larger_than_the_cost_is_refused(company: Company):
    credit = CostItem(label="환입", category="credit", quantity=D("10"), unit="kg", unit_price=D("1"))
    with pytest.raises(PrecostInputError):
        piece_costs((ITEMS[0], credit), company)


def test_labor_is_its_own_line(ruleset: Ruleset, company: Company):
    """Labor: hours per piece × wage rate, shown as its own line and taking the scrap and SG&A uplift."""
    labor = CostItem(label="조립 작업자", category="labor", quantity=D("0.01"), unit="h", unit_price=D("30"))
    piece = piece_costs((*ITEMS, labor), company)
    assert piece["lines"]["labor"] == D("0.3")
    assert piece["piece_cost"] == D("3.11") * UPLIFT
    lines = {line["category"]: line for line in run(ruleset, company, items=(*ITEMS, labor))["cost"]["lines"]}
    assert (lines["labor"]["label"], lines["labor"]["value"]) == ("노무비", "0.3000")


# ── lifetime minimum quote ──────────────────────────────────────────────────
def test_lifetime_factor_applies_the_reduction_from_the_second_year():
    assert lifetime_factor(inputs()) == (D(1) + D("0.97") + D("0.97") ** 2) / 3
    assert (
        lifetime_factor(inputs(reduction_starts_at_index=0))
        == (D("0.97") + D("0.97") ** 2 + D("0.97") ** 3) / 3
    )


def test_minimum_quote_keeps_the_margin_on_average_over_the_lifetime(ruleset: Ruleset, company: Company):
    result = run(ruleset, company)
    lf = lifetime_factor(inputs())
    expected = D("2.81") * UPLIFT * D("1.05") / lf
    assert result["cost"]["min_quote"] == format(expected.quantize(D("0.0001")), "f")
    assert result["cost"]["piece_cost"] == "3.0803"


def test_indexed_material_is_not_reduced(ruleset: Ruleset, company: Company):
    """With raw-material indexation the material part follows the market: only the rest is reduced."""
    plain = D(run(ruleset, company)["cost"]["min_quote"])
    indexed = run(ruleset, company, material_indexed=True)["cost"]
    lf = lifetime_factor(inputs())
    material = (D("2.10") - D("0.06")) * UPLIFT
    expected = material + (D("2.81") * UPLIFT * D("1.05") - material) / lf
    assert indexed["material_indexed"] is True
    assert D(indexed["min_quote"]) == expected.quantize(D("0.0001"))
    assert D(indexed["min_quote"]) < plain


def test_amortized_tooling_is_added_per_piece(ruleset: Ruleset, company: Company):
    tooling = {"amount": D("30000"), "payment": "amortized", "deferred_years": 0}
    result = run(ruleset, company, tooling=tooling)["cost"]
    assert result["tooling_per_piece"] == "0.1000"
    lf = lifetime_factor(inputs())
    assert D(result["min_quote"]) == ((D("2.81") * UPLIFT + D("0.1")) * D("1.05") / lf).quantize(D("0.0001"))


def test_zero_volume_is_refused(ruleset: Ruleset, company: Company):
    with pytest.raises(PrecostInputError):
        run(ruleset, company, volumes=[{"year": 2028, "quantity": 0}])


# ── shocks and the safe quote ───────────────────────────────────────────────
def test_shocks_widen_the_range_and_the_fx_shock_needs_a_foreign_currency(ruleset: Ruleset, company: Company):
    result = run(ruleset, company)["cost"]
    shocks = {s["id"]: s for s in result["shocks"]}
    assert D(result["safe_quote"]) > D(result["min_quote"])
    assert shocks["raw_material"]["applied"] and D(shocks["raw_material"]["min_quote_delta"]) > 0
    assert shocks["fx"]["applied"], "EUR quote, KRW company"
    assert shocks["interest"]["note"].startswith("NPV -"), "a higher rate lowers the NPV"

    home = company.model_copy(
        update={"settings": company.settings.model_copy(update={"home_currency": "EUR"})}
    )
    same = {s["id"]: s for s in run(ruleset, home)["cost"]["shocks"]}
    assert not same["fx"]["applied"] and "같아" in same["fx"]["note"]


def test_a_pass_through_clause_removes_its_shock(ruleset: Ruleset, company: Company):
    context = RfqCostContext(pass_through=("indexation",), classification=MANUFACTURING)
    shocks = {s["id"]: s for s in run(ruleset, company, context)["cost"]["shocks"]}
    assert not shocks["raw_material"]["applied"] and "원자재 연동" in shocks["raw_material"]["note"]
    overridden = {
        s["id"]: s for s in run(ruleset, company, context, material_indexed=False)["cost"]["shocks"]
    }
    assert overridden["raw_material"]["applied"], "the user's answer wins over the RFQ text"


# ── NPV (reference) ─────────────────────────────────────────────────────────
def test_cash_flows_npv_and_payback():
    """Tooling built in the year before SOP and paid back as a lump sum at SOP."""
    tooling = {"amount": D("1000"), "payment": "lump_sum", "deferred_years": 0}
    data = inputs(
        volumes=[{"year": 2028, "quantity": 100}, {"year": 2029, "quantity": 100}],
        annual_reduction_rate=D(0),
        tooling=tooling,
    )
    flows = cash_flows(data, D("3"), D("2"))
    assert flows == [(0, 2027, D(-1000)), (1, 2028, D(1100)), (2, 2029, D(100))]
    assert npv(flows, D("0.1")) == D(-1000) + D(1100) / D("1.1") + D(100) / D("1.21")
    assert payback(flows) == 2028
    deferred = cash_flows(
        data.model_copy(
            update={"tooling": data.tooling.model_copy(update={"payment": "deferred", "deferred_years": 3})}
        ),
        D("3"),
        D("2"),
    )
    assert deferred[-1] == (4, 2031, D(1000)) and payback(deferred) == 2031


# ── conflicts (INV-13) ──────────────────────────────────────────────────────
def test_conflicts_give_one_case_per_value_and_resolving_one_narrows_them(ruleset: Ruleset, company: Company):
    context = RfqCostContext(issues=(THICKNESS, INCOTERM), classification=MANUFACTURING)
    result = run(ruleset, company, context)["cost"]
    assert [s["label"] for s in result["scenarios"]] == ["2.5 · FCA", "2.5 · DDP", "2 · FCA", "2 · DDP"]
    by = {s["label"]: D(s["min_quote"]) for s in result["scenarios"]}
    assert by["2.5 · DDP"] == max(by.values()) == D(result["min_quote"]), "the worst case is reported"
    assert by["2 · FCA"] < by["2 · DDP"] < by["2.5 · DDP"]
    one = run(ruleset, company, context, resolved={"c-thickness": "2"})["cost"]
    assert [s["label"] for s in one["scenarios"]] == ["FCA", "DDP"]
    assert D(one["min_quote"]) == by["2 · DDP"], "quantities stay those of the first stated value (2.5)"
    with pytest.raises(PrecostInputError):
        run(ruleset, company, context, resolved={"c-thickness": "3"})
    with pytest.raises(PrecostInputError):
        run(ruleset, company, context, resolved={"c-nothing": "1"})


def test_a_conflict_no_driver_can_evaluate_holds_the_decision(ruleset: Ruleset, company: Company):
    context = RfqCostContext(
        issues=(issue("c-target", "target_price", "1.2", "1.3"),), classification=MANUFACTURING
    )
    decision = run(ruleset, company, context)["decision"]
    assert (decision["status"], decision["rule"]) == ("hold", "unmodelled_conflict")
    chosen = run(ruleset, company, context, resolved={"c-target": "1.2"})["decision"]
    assert chosen["rule"] != "unmodelled_conflict"


def test_cases_that_disagree_hold_the_decision(ruleset: Ruleset, company: Company):
    context = RfqCostContext(issues=(THICKNESS,), classification=MANUFACTURING)
    cases = {s["label"]: D(s["min_quote"]) for s in run(ruleset, company, context)["cost"]["scenarios"]}
    between = (cases["2"] + cases["2.5"]) / 2
    decision = run(ruleset, company, context, target_price=between)["decision"]
    assert (decision["status"], decision["rule"]) == ("hold", "conflict_dependent")


# ── decision order ──────────────────────────────────────────────────────────
def test_no_target_gives_the_minimum_bid_instead_of_a_decision(ruleset: Ruleset, company: Company):
    decision = run(ruleset, company, target_price=None)["decision"]
    assert (decision["status"], decision["rule"], decision["score"]) == (
        "baseline_undefined",
        "no_target",
        None,
    )
    assert "최소 입찰가" in decision["reasons"][0]


def test_the_total_quote_sums_every_year_at_its_reduced_price(ruleset: Ruleset, company: Company):
    cost = run(ruleset, company)["cost"]
    years = D(1) + D("0.97") + D("0.97") ** 2
    for key in ("min_quote", "safe_quote"):
        expected = D(cost[key]) * 100_000 * years
        assert abs(D(cost["totals"][key]) - expected) < D("0.01") * 100_000
    assert cost["total_quantity"] == 300_000
    assert cost["cost_basis"] == "manufacturing" and cost["markup"] is None


DISTRIBUTION = {**MANUFACTURING, "type": "distribution", "label": "유통·재판매", "baseline": "none"}
RESALE_ITEMS = (
    CostItem(
        label="매입 부품 (100 USD어치)",
        category="purchased",
        quantity=D("1"),
        unit="단위",
        unit_price=D("100"),
    ),
    CostItem(label="카운터 인력", category="labor", quantity=D("0.2"), unit="h", unit_price=D("30")),
    CostItem(label="배송", category="logistics", quantity=D("1"), unit="단위", unit_price=D("3")),
)


def test_distribution_adds_no_scrap_or_sga_and_reports_the_markup(ruleset: Ruleset, company: Company):
    """Resale: purchase + operating costs, margin only; the quote is also a markup over the purchase."""
    context = RfqCostContext(classification=DISTRIBUTION)
    result = run(
        ruleset,
        company,
        context,
        items=RESALE_ITEMS,
        target_price=None,
        annual_reduction_rate=D(0),
        currency="KRW",
    )
    cost = result["cost"]
    assert cost["cost_basis"] == "resale"
    assert cost["piece_cost"] == "109.0000"
    assert "overhead" not in {line["category"] for line in cost["lines"]}
    assert cost["min_quote"] == "114.4500"  # 109 × 1.05, no reduction
    assert cost["markup"]["purchased"] == "100.0000"
    assert cost["markup"]["min_quote"] == "0.1445"
    assert cost["totals"]["min_quote"] == "34335000.0000"  # 300 000 units × 114.45
    assert "총액" in result["decision"]["reasons"][0]
    assert run(ruleset, company, items=RESALE_ITEMS)["cost"]["piece_cost"] == money_of(D(109) * UPLIFT)


def money_of(value: Decimal) -> str:
    return format(value.quantize(D("0.0001")), "f")


def test_a_target_below_every_minimum_quote_is_a_price_knockout(ruleset: Ruleset, company: Company):
    decision = run(ruleset, company, target_price=D("1"))["decision"]
    assert (decision["status"], decision["rule"]) == ("no_go", "price_knockout")


def test_a_type_that_is_not_judged_automatically(ruleset: Ruleset, company: Company):
    other = {**MANUFACTURING, "type": "other", "automatic": False, "note": "수기로 검토"}
    decision = run(ruleset, company, RfqCostContext(classification=other))["decision"]
    assert (decision["status"], decision["rule"]) == ("not_applicable", "rfq_type")


def test_score_bands(ruleset: Ruleset, company: Company):
    """A target well above the safe quote with good terms is a Go; one just above the minimum is not."""
    high = run(
        ruleset, company, target_price=D("6"), payment_days=30, capacity_utilization=D("0.7"), strategic=100
    )
    assert high["decision"]["status"] == "go" and high["score"]["total"] >= 70
    assert [i["id"] for i in high["score"]["items"]] == [
        "profitability",
        "contract_risk",
        "cash_flow",
        "capacity",
        "technical_fit",
        "strategic",
    ]
    low_target = D(run(ruleset, company)["cost"]["min_quote"]) + D("0.001")
    low = run(
        ruleset,
        company,
        target_price=low_target,
        payment_days=180,
        capacity_utilization=D("1.2"),
        strategic=0,
        context=RfqCostContext(red_flag_types=("a", "b", "c"), classification=MANUFACTURING),
    )
    assert low["decision"]["status"] == "no_go" and low["decision"]["rule"] == "score"


# ── knockout 1 ──────────────────────────────────────────────────────────────
def rfq_clauses():
    return [
        make_clause("1", ["1 The supplier shall be certified to IATF 16949 and ISO 14001."]),
        make_clause("2", ["2 The housing is produced by high-pressure die casting."], first_sentence=2),
        make_clause("3", ["3 The supplier accepts unlimited liability for recalls."], first_sentence=3),
        make_clause("4", ["4 Payment terms: 120 days net."], first_sentence=4),
        make_clause(
            "5", ["5 Aluminium prices are adjusted by the LME index every quarter."], first_sentence=5
        ),
    ]


def payment_mention(clauses) -> EntityMention:
    sentence = clauses[3].sentences[0]
    return EntityMention(
        entity="payment_days", raw="120 days", normalized="120", locus=sentence.locus, text=sentence.text
    )


def test_requirements_are_found_cited_and_checked_against_the_profile(ruleset: Ruleset, company: Company):
    clauses = rfq_clauses()
    profiled = company.model_copy(
        update={
            "profile": company.profile.model_copy(update={"certifications": ("IATF16949",)}),
            "settings": company.settings.model_copy(update={"max_payment_days": 90}),
            "answers": (StoredAnswer(key="certification:ISO14001", label="", answer="no", saved_on=TODAY),),
        }
    )
    checks = {c["key"]: c for c in requirements(clauses, [payment_mention(clauses)], profiled, ruleset)}
    assert set(checks) == {
        "certification:IATF16949",
        "certification:ISO14001",
        "process:다이캐스팅",
        "liability:UNLIMITEDLIABILITY",
        "payment:120",
    }
    assert (checks["certification:IATF16949"]["status"], checks["certification:IATF16949"]["source"]) == (
        "pass",
        "profile",
    )
    assert (checks["certification:ISO14001"]["status"], checks["certification:ISO14001"]["source"]) == (
        "fail",
        "stored",
    )
    assert (
        checks["process:다이캐스팅"]["status"] == "ask"
        and checks["process:다이캐스팅"]["department"] == "production_engineering"
    )
    assert checks["liability:UNLIMITEDLIABILITY"]["kind"] == "judgment"
    assert checks["payment:120"]["status"] == "ask" and "90일" in checks["payment:120"]["question"]
    assert all(c["evidence"]["citation_status"] == "verified" for c in checks.values())

    within = profiled.model_copy(
        update={"settings": profiled.settings.model_copy(update={"max_payment_days": 120})}
    )
    again = {c["key"]: c for c in requirements(clauses, [payment_mention(clauses)], within, ruleset)}
    assert again["payment:120"]["status"] == "pass"


def test_the_same_requirement_in_singular_and_plural_is_asked_once(ruleset: Ruleset, company: Company):
    """ "authorized Ford Dealers" in one sentence and "authorized Ford Dealer" in another: one question."""
    clauses = [
        make_clause("1", ["1 Only authorized Ford Dealers may submit a quote."]),
        make_clause("2", ["2 The bidder shall be an authorized Ford Dealer."], first_sentence=2),
    ]
    checks = [c for c in requirements(clauses, [], company, ruleset) if c["requirement"] == "qualification"]
    assert [(c["key"], c["value"]) for c in checks] == [
        ("qualification:AUTHORIZEDFORDDEALER", "authorized Ford Dealers")
    ]
    profiled = company.model_copy(
        update={"profile": company.profile.model_copy(update={"qualifications": ("Authorized Ford dealer",)})}
    )
    [check] = [c for c in requirements(clauses, [], profiled, ruleset) if c["requirement"] == "qualification"]
    assert check["status"] == "pass"


def test_answers_decide_the_knockout(ruleset: Ruleset, company: Company):
    clauses = rfq_clauses()
    checks = tuple(requirements(clauses, [], company, ruleset))
    context = RfqCostContext(requirements=checks, classification=MANUFACTURING)
    yes = {c["key"]: "yes" for c in checks}

    passed = run(ruleset, company, context, answers=yes, target_price=D("6"))
    assert passed["knockout"]["status"] == "passed" and passed["decision"]["rule"] == "score"

    no = run(ruleset, company, context, answers={**yes, "process:다이캐스팅": "no"})
    assert (no["knockout"]["status"], no["decision"]["status"], no["decision"]["rule"]) == (
        "failed",
        "no_go",
        "knockout",
    )

    unknown = run(
        ruleset,
        company,
        context,
        answers={**yes, "liability:UNLIMITEDLIABILITY": "unknown"},
        target_price=D("6"),
        payment_days=30,
        capacity_utilization=D("0.7"),
        strategic=100,
    )
    assert unknown["knockout"]["status"] == "open"
    assert unknown["decision"]["status"] == "conditional", "an unknown answer never knocks out, but caps a Go"
    assert any("판단 불가" in r for r in unknown["decision"]["reasons"])


# ── setup: type, pass-through, suggestions ──────────────────────────────────
def test_the_rfq_type_comes_from_the_patterns(ruleset: Ruleset):
    oem = [
        make_clause(
            "1", ["SOP is planned for 03/2028.", "PPAP level 3 is required.", "Tooling is paid at ISIR."]
        )
    ]
    assert classify(oem, ruleset)["type"] == "manufacturing"
    dealer = [
        make_clause(
            "1",
            [
                "Only authorized Ford dealers may bid.",
                "This is a brand name specification.",
                "Complete the price sheet.",
                "Delivery F.O.B. Destination.",
            ],
        )
    ]
    found = classify(dealer, ruleset)
    assert (found["type"], found["decision_unit"], found["baseline"]) == ("distribution", "item", "none")
    assert classify([make_clause("1", ["Hello."])], ruleset)["type"] == "other"


def test_pass_through_clauses_are_cited(ruleset: Ruleset):
    found = pass_through(rfq_clauses(), ruleset)
    assert found["indexation"]["evidence"]["citation_status"] == "verified"
    assert found["fx_adjustment"] is None


def test_suggested_items_come_from_the_price_table(company: Company):
    row = PriceRow(
        id="price-000000000001",
        category="material",
        name="고장력강판 HC420LA",
        unit="kg",
        price=D("1.05"),
        currency="EUR",
        valid_until=date(2026, 1, 1),
        department="purchasing",
    )
    priced = company.model_copy(update={"prices": (row,)})
    cost_inputs = {
        "candidates": [{"field": "material", "value": "hc420la"}, {"field": "net_weight", "value": "0.85"}]
    }
    [item] = suggested_items(cost_inputs, priced, TODAY)
    assert (item["price_id"], item["quantity"], item["unit_price"], item["expired"]) == (
        "price-000000000001",
        "0.85",
        "1.05",
        True,
    )
    assert suggested_items({"candidates": []}, priced, TODAY) == []


def test_price_rows_must_match_the_quote_currency_and_expired_rows_warn(ruleset: Ruleset, company: Company):
    row = PriceRow(
        id="price-000000000001",
        category="material",
        name="Steel",
        unit="kg",
        price=D("1500"),
        currency="KRW",
        valid_until=date(2026, 1, 1),
        department="purchasing",
    )
    priced = company.model_copy(update={"prices": (row,)})
    item = ITEMS[0].model_copy(update={"price_id": row.id})
    with pytest.raises(PrecostInputError, match="통화"):
        run(ruleset, priced, items=(item, *ITEMS[1:]))
    euro = priced.model_copy(
        update={"prices": (row.model_copy(update={"currency": "EUR", "price": D("1.05")}),)}
    )
    reasons = run(ruleset, euro, items=(item, *ITEMS[1:]))["decision"]["reasons"]
    assert any("유효기간" in r for r in reasons)


# ── company settings ────────────────────────────────────────────────────────
def test_company_store_keeps_rows_and_remembers_answers(tmp_path, ruleset: Ruleset):
    store = CompanyStore(tmp_path, ruleset)
    record = store.company.model_dump(mode="json")
    row = {
        "id": None,
        "category": "process",
        "name": "프레스 400톤",
        "unit": "h",
        "price": "180",
        "currency": "EUR",
        "valid_until": "2099-12-31",
        "department": "production_engineering",
    }
    saved = store.replace({**record, "prices": [row]})
    assert saved.prices[0].id.startswith("price-")
    store.remember([StoredAnswer(key="process:다이캐스팅", label="?", answer="no", saved_on=TODAY)])
    reopened = CompanyStore(tmp_path, ruleset).company
    assert reopened.prices == saved.prices and reopened.answer("process:다이캐스팅").answer == "no"
    again = store.replace({**reopened.model_dump(mode="json")})
    assert again.answer("process:다이캐스팅") is not None, "saving the settings keeps the answers"
    with pytest.raises(CompanyError):
        validate(
            {**record, "prices": [{**row, "id": "price-ffffffffffff"}]},
            ruleset=ruleset,
            current=store.company,
        )
    with pytest.raises(CompanyError):
        validate(
            {**record, "settings": {**record["settings"], "discount_rate": "2"}},
            ruleset=ruleset,
            current=store.company,
        )


def test_inputs_refuse_floats_and_bad_categories():
    with pytest.raises(ValidationError):
        inputs(items=[{"label": "a", "category": "steel", "quantity": "1", "unit": "kg", "unit_price": "1"}])
    with pytest.raises(ValidationError):
        inputs(annual_reduction_rate=D("1"))


# ── cost inputs from the RFQ ────────────────────────────────────────────────
def package():
    price = make_clause("4.2", ["4.2 The target price is 1.20 EUR per piece."])
    spec = make_clause("3.1", ["3.1 Sheet thickness 2.5 mm."], source_doc="d02")
    cut = make_clause(
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
    clauses = [price, cut, spec, sheet]
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
            locus=cut.sentences[0].locus,
            text=cut.text,
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
            "cost_fields": ("thickness",),
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
    """COST-05: RFQ values are cited; conflicts list alternatives; the rest is missing, never assumed."""
    parsed, clauses, mentions, issues = package()
    data = extract_cost_inputs(make_manifest(), parsed, clauses, mentions, issues, ruleset)
    assert_valid("cost-inputs", {"data": data, "provenance": {"cost_inputs": "real"}})
    assert [c["field"] for c in data["candidates"]] == list(COST_FIELDS)
    by = {c["field"]: c for c in data["candidates"]}
    assert data["item"] == "AB-100"
    assert (by["target_price"]["value"], by["target_price"]["source"]) == ("1.2", "rfq")
    assert by["target_price"]["evidence"]["finding"]["citations"][0]["section_id"] == "4.2"
    thickness = by["thickness"]
    assert thickness["value"] is None and thickness["issue_id"] == "c-thickness"
    assert [a["value"] for a in thickness["alternatives"]] == ["2.5", "2"]
    volume = by["volume"]
    assert (volume["value"], volume["source"]) == ("320000", "rfq") and "구매 확약이 아니" in volume["note"]
    assert data["volumes"] == [
        {"year": 2028, "quantity": 60000},
        {"year": 2029, "quantity": 120000},
        {"year": 2030, "quantity": 140000},
    ]
    assert (data["annual_reduction_rate"], data["reduction_starts_at_index"]) == ("0.03", 1)
    assert (by["payment_days"]["source"], by["payment_days"]["value"]) == ("missing", None)


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
    """COST-09: "2. Jahr" is the second year; "2.5 %" is a rate, not an ordinal."""
    mention = EntityMention(
        entity="annual_reduction_rate",
        raw=f"{rate} %",
        normalized=rate,
        unit="%",
        locus=make_locus(1),
        text=text,
    )
    assert reduction([mention]) == (D(rate) / 100, starts)
