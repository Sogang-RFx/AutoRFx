"""What a scope sees of a package, at the level of :mod:`autorfx.access` (docs/auth-rbac.md §2, §4)."""

from __future__ import annotations

import json

import pytest

from auth_helpers import LIMITED, SECRET, scope_for, visible_of
from autorfx import access
from autorfx.analysis import RuleOnlyProvider, analyze
from autorfx.consistency.checks import MAX_EVIDENCE
from autorfx.models import ClauseAnalysis, ConsistencyIssue, IssueEvidence, ParsedFile, SectionInfo
from autorfx.rules import Ruleset
from factories import make_clause, make_locus, make_sentence

LOGISTICS = make_clause("1.1", ["1.1 Packaging in small load carrier."], title="Inbound flow")
QUALITY = make_clause("1.2", ["1.2 An IATF 16949 certification is required."], first_sentence=2)
BOTH = make_clause("1.3", ["1.3 Audit of packaging and container inspection."], first_sentence=3)
COST = make_clause(
    "1.4",
    ["1.4 Quality inspection surcharge: target price is 9.87 EUR."],
    first_sentence=4,
    is_cost_sensitive=True,
)
CLAUSES = (LOGISTICS, QUALITY, BOTH, COST)


def judged(clause, primary: str, *reference: str, cites=None) -> ClauseAnalysis:  # type: ignore[no-untyped-def]
    """An analysis with a chosen routing, so a test does not depend on keyword weights."""
    from autorfx.models import Finding, VerifiedFinding

    locus = (cites or clause).sentences[0].locus
    return ClauseAnalysis(
        clause_id=clause.clause_id,
        primary_department=primary,
        reference_departments=tuple(reference),
        priority="must",
        summary=VerifiedFinding(
            finding=Finding(kind="summary", departments=(primary,), text="", citations=(locus,)),
            citation_status="verified",
        ),
        origin="rule",
    )


ANALYSES = (
    judged(LOGISTICS, "logistics"),
    judged(QUALITY, "quality"),
    judged(BOTH, "quality", "logistics"),
    judged(COST, "costing", "quality", "purchasing"),
)


def ids(name: str, ruleset: Ruleset, clauses=CLAUSES, analyses=ANALYSES) -> list[str]:  # type: ignore[no-untyped-def]
    found = visible_of(clauses, analyses, scope_for(name, ruleset), ruleset)
    assert isinstance(found, tuple)
    return [c.section_id for c in found]


def items(name: str, ruleset: Ruleset, clauses=CLAUSES, analyses=ANALYSES, department=None) -> list[dict]:  # type: ignore[no-untyped-def]
    return access.clause_items(
        clauses,
        analyses,
        scope=scope_for(name, ruleset),
        ruleset=ruleset,
        department=department,
        secret=SECRET,
    )


# ── visible clauses ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("admin", ["1.1", "1.2", "1.3", "1.4"]),
        ("sales", ["1.1", "1.2", "1.3", "1.4"]),
        ("logistics", ["1.1", "1.3"]),
        ("quality", ["1.2", "1.3"]),
        ("costing", ["1.4"]),
        ("purchasing", ["1.4"]),
        ("legal", []),
    ],
)
def test_a_scope_sees_lead_and_reference_clauses_it_may_read(
    ruleset: Ruleset, name: str, expected: list[str]
):
    """auth-rbac §2: lead or reference department, minus cost clauses for those who may not see cost."""
    assert ids(name, ruleset) == expected


def test_a_cost_clause_is_a_lock_for_a_department_that_is_its_reference(ruleset: Ruleset):
    """INV-8: the cost rule wins over the department; the lock does not say which rule applied."""
    locked = [i for i in items("quality", ruleset) if i["kind"] == "masked"]
    assert locked == [
        {"kind": "masked", "clause_id": "d01:1.1", "source_doc": "d01", "section_id": "1.1"},
        {"kind": "masked", "clause_id": "d01:1.4", "source_doc": "d01", "section_id": "1.4"},
    ]


def test_a_clause_without_an_analysis_is_not_visible(ruleset: Ruleset):
    """Fail closed: nothing says whose clause it is, so not even a department that gets everything sees it."""
    for name in ("admin", "sales", "quality"):
        assert "1.2" not in ids(name, ruleset, analyses=(ANALYSES[0], *ANALYSES[2:]))


def test_a_clause_that_cites_a_hidden_one_is_hidden_until_nothing_changes(ruleset: Ruleset):
    """INV-8, fail closed, to a fixed point: 1.3 cites 1.2 which cites 1.1; hiding 1.1 hides all three."""
    chain = (
        judged(LOGISTICS, "logistics"),
        judged(QUALITY, "quality", cites=LOGISTICS),
        judged(BOTH, "quality", cites=QUALITY),
        judged(COST, "costing"),
    )
    assert ids("quality", ruleset, analyses=chain) == []
    # The order of the clauses does not matter for the result.
    assert ids("quality", ruleset, clauses=CLAUSES[::-1], analyses=chain[::-1]) == []
    assert ids("admin", ruleset, analyses=chain) == ["1.1", "1.2", "1.3", "1.4"]


def test_the_judged_department_decides_not_the_work_area_keywords(ruleset: Ruleset):
    """auth-rbac §2: work-area keywords are a view aid; a clause full of logistics words that is routed
    to legal only is not logistics' clause."""
    clause = make_clause("5.1", ["5.1 Packaging, delivery, container, freight and safety stock."])
    legal_only = (judged(clause, "legal"),)
    assert ids("logistics", ruleset, (clause,), legal_only) == []
    assert items("logistics", ruleset, (clause,), legal_only)[0]["kind"] == "masked"


# ── rows ────────────────────────────────────────────────────────────────────
def test_rtm_and_clauses_lock_the_same_rows(ruleset: Ruleset):
    """INV-8: one decision for every surface."""
    for name in ("admin", "sales", *LIMITED):
        rows = access.rtm_rows(
            CLAUSES, ANALYSES, scope=scope_for(name, ruleset), ruleset=ruleset, secret=SECRET
        )
        locked = [r for r in rows if r["kind"] == "masked"]
        assert locked == [i for i in items(name, ruleset) if i["kind"] == "masked"], name
        assert len(rows) == 4


def test_the_department_filter_moves_visible_rows_only(ruleset: Ruleset):
    """INV-8: the locked rows are the same, in the same places, under every filter."""
    for department in (None, *ruleset.department_ids, "unknown"):
        found = items("quality", ruleset, department=department)
        assert [i["section_id"] for i in found if i["kind"] == "masked"] == ["1.1", "1.4"]
    assert [i["section_id"] for i in items("quality", ruleset, department="logistics")] == [
        "1.1",
        "1.3",
        "1.4",
    ]
    assert [i["section_id"] for i in items("quality", ruleset, department="legal")] == ["1.1", "1.4"]


def test_an_inherited_title_is_blank_outside_the_full_scope(ruleset: Ruleset):
    """auth-rbac §4: the heading above may belong to another department; the clause's own words may stay."""
    inherited = make_clause("2.1", ["2.1 Packaging in small load carrier."], title="Tooling ownership")
    stated = make_clause("2.2", ["2.2 Packaging rules apply."], title="Packaging rules", first_sentence=2)
    heading = make_sentence("2.3 Container care", 3, role="heading", section_id="2.3")
    body = make_sentence("Use a container.", 4, section_id="2.3")
    own = make_clause("2.3", ["x"], title="Container care").model_copy(update={"sentences": (heading, body)})
    clauses = (inherited, stated, own)
    analyses = tuple(judged(c, "logistics") for c in clauses)
    assert [i["title"] for i in items("logistics", ruleset, clauses, analyses)] == [
        "",
        "Packaging rules",
        "Container care",
    ]
    assert [i["title"] for i in items("sales", ruleset, clauses, analyses)] == [c.title for c in clauses]
    rows = access.rtm_rows(
        clauses, analyses, scope=scope_for("logistics", ruleset), ruleset=ruleset, secret=SECRET
    )
    assert [r["title"] for r in rows] == ["", "Packaging rules", "Container care"]


def test_a_cost_title_stays_hidden_even_when_it_is_the_clauses_own(ruleset: Ruleset):
    """INV-8: the cost rule for titles is kept."""
    clause = make_clause("3.1", ["3.1 Price list in packaging."], title="Price list")
    (row,) = items("logistics", ruleset, (clause,), (judged(clause, "logistics"),))
    assert row["kind"] == "clause" and row["title"] == ""


# ── sentences ───────────────────────────────────────────────────────────────
def test_sentences_are_an_allowlist_of_the_visible_clauses(ruleset: Ruleset):
    """CITE-03, INV-8: a heading in no clause goes to the full scope only."""
    heading = make_sentence("1 Tooling ownership", 9, role="heading", section_id="1")
    parsed = ParsedFile(
        file_id="d01",
        lang="en",
        page_count=1,
        sentences=(heading, *(s for c in CLAUSES for s in c.sentences)),
        sections=(SectionInfo(section_id="1", heading_only=True),),
    )

    def texts(name: str) -> list[str]:
        scope = scope_for(name, ruleset)
        visible = visible_of(CLAUSES, ANALYSES, scope, ruleset)
        found = access.sentences_for(parsed, visible, scope=scope, page_number=1)
        return [s["text"][:3] for s in found]

    assert texts("admin") == texts("sales") == ["1 T", "1.1", "1.2", "1.3", "1.4"]
    assert texts("logistics") == ["1.1", "1.3"]
    assert texts("legal") == []


# ── consistency ─────────────────────────────────────────────────────────────
def issue(issue_id: str, *clauses, **change: object) -> ConsistencyIssue:  # type: ignore[no-untyped-def]
    return ConsistencyIssue(
        id=issue_id,
        category="conflict",
        severity="high",
        title=f"title of {issue_id}",
        detail=f"detail of {issue_id}",
        evidence=tuple(IssueEvidence(locus=c.sentences[0].locus, text=c.sentences[0].text) for c in clauses),
        **change,  # type: ignore[arg-type]
    )


def shown(name: str, ruleset: Ruleset, issues) -> list[dict]:  # type: ignore[no-untyped-def]
    scope = scope_for(name, ruleset)
    visible = visible_of(CLAUSES, ANALYSES, scope, ruleset)
    return access.issue_items(issues, scope=scope, visible=visible, secret=SECRET)


def test_an_issue_is_shown_when_all_its_evidence_is_visible(ruleset: Ruleset):
    """auth-rbac §4: one hidden sentence locks the issue; locks come last and carry an opaque id only."""
    issues = (issue("c-one", LOGISTICS, QUALITY), issue("c-two", LOGISTICS, BOTH), issue("c-three", BOTH))
    found = shown("logistics", ruleset, issues)
    assert [i.get("id") for i in found[:2]] == ["c-two", "c-three"]
    assert set(found[2]) == {"kind", "id"} and found[2]["id"].startswith("hidden-")
    assert "c-one" not in json.dumps(found) and "title of c-one" not in json.dumps(found)
    assert [i["id"] for i in shown("sales", ruleset, issues)] == ["c-one", "c-two", "c-three"]


def test_an_issue_whose_evidence_may_be_cut_off_is_locked(ruleset: Ruleset):
    """auth-rbac §4: the title and detail name every value, but only MAX_EVIDENCE sentences are kept, so
    at that count a value of a clause outside the scope may be among the unnamed rest."""
    full = [make_sentence(f"packaging {n}", 20 + n, section_id="1.1") for n in range(MAX_EVIDENCE)]
    clause = make_clause("6.1", ["x"]).model_copy(update={"sentences": tuple(full)})

    def many(count: int) -> ConsistencyIssue:
        evidence = tuple(IssueEvidence(locus=s.locus, text=s.text) for s in full[:count])
        return issue("c-many", clause).model_copy(update={"evidence": evidence})

    for name, count, kind in (
        ("logistics", MAX_EVIDENCE - 1, "issue"),
        ("logistics", MAX_EVIDENCE, "masked"),
        ("sales", MAX_EVIDENCE, "issue"),
    ):
        scope = scope_for(name, ruleset)
        (found,) = access.issue_items([many(count)], scope=scope, visible=(clause,), secret=SECRET)
        assert found["kind"] == kind, (name, count)


def test_a_cost_sensitive_issue_stays_hidden_from_who_may_not_see_cost(ruleset: Ruleset):
    """INV-8: the flag of the pipeline is kept, even when every sentence is visible."""
    flagged = issue("c-cost", BOTH, is_cost_sensitive=True)
    assert shown("logistics", ruleset, [flagged])[0]["kind"] == "masked"
    assert shown("sales", ruleset, [flagged])[0]["kind"] == "issue"


def test_an_issue_without_evidence_is_locked(ruleset: Ruleset):
    """Fail closed: "all of nothing" is not a reason to show an issue."""
    assert shown("logistics", ruleset, [issue("c-none")])[0]["kind"] == "masked"


def test_evidence_in_no_clause_is_not_visible(ruleset: Ruleset):
    """INV-8, allowlist."""
    stray = issue("c-stray", BOTH).model_copy(
        update={"evidence": (IssueEvidence(locus=make_locus(40, section_id="9"), text="stray"),)}
    )
    assert shown("logistics", ruleset, [stray])[0]["kind"] == "masked"


def test_withheld_ids_are_exactly_the_locked_rows(ruleset: Ruleset):
    """auth-rbac §4: evaluation hides the rows of every clause and issue that is locked elsewhere."""
    issues = (issue("c-one", LOGISTICS, QUALITY), issue("c-three", BOTH))
    for name, expected in (
        ("sales", set()),
        ("logistics", {"d01:1.2", "d01:1.4", "c-one"}),
        ("legal", {"d01:1.1", "d01:1.2", "d01:1.3", "d01:1.4", "c-one", "c-three"}),
    ):
        scope = scope_for(name, ruleset)
        assert access.withheld_ids(CLAUSES, ANALYSES, issues, scope=scope, ruleset=ruleset) == expected


def test_rule_routing_still_feeds_the_scope(ruleset: Ruleset):
    """ROUTE-01: with the real rules a logistics sentence is logistics' clause (no hand-made routing)."""
    results = analyze(CLAUSES, ruleset, RuleOnlyProvider())
    assert "1.1" in ids("logistics", ruleset, analyses=results)
    assert "1.2" in ids("quality", ruleset, analyses=results)
