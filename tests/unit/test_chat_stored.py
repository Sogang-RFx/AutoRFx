"""Answers from stored results: red flags, conflicts, and what a restricted team may learn (§3.4).

Asked through ``answer_question`` without a model, so these guarantees hold with no LLM (INV-8, INV-12).
"""

from __future__ import annotations

import pytest

from autorfx.rules import Ruleset
from chat_helpers import (
    CLAUSES,
    DELIVERY,
    PRICE,
    QUALITY,
    SECRETS,
    WARRANTY,
    ask,
    cited,
    dumped,
    make_context,
)
from factories import make_analysis, make_issue, make_locus, make_sentence

FLAGS = "독소조항을 알려줘"
CONFLICTS = "문서끼리 다른 값이 있나요?"
NO_FLAGS = "분석에서 표시된 독소조항이 없습니다."
NO_CONFLICTS = "문서 사이에서 찾은 불일치가 없습니다."
VISIBLE = [c for c in CLAUSES if c is not PRICE]
RESTRICTED_GROUPS = ["cost_restricted", "auditors"]

WARRANTY_FLAG = make_analysis(WARRANTY, label="무제한 보증", reason="기간 상한이 없습니다")
PRICE_FLAG = make_analysis(PRICE, label="CR 강제", reason="목표가 1.2345 EUR 초과 견적은 탈락합니다")
TERMS = make_issue("c-terms", [DELIVERY.sentences[1], QUALITY.sentences[0]], title="인도조건이 다릅니다")
HIDDEN_ISSUE = make_issue(
    "c-target_price", [PRICE.sentences[0], QUALITY.sentences[0]], title="목표가 1.2345 EUR이 다릅니다"
)


# ── red flags ───────────────────────────────────────────────────────────────
def test_red_flags_are_listed_with_their_citations(ruleset: Ruleset):
    """INV-3, INV-12: one statement per flagged clause, marked as stored, no model involved."""
    context = make_context(ruleset, analyses=[make_analysis(DELIVERY), PRICE_FLAG, WARRANTY_FLAG])
    data = ask(context, FLAGS)
    assert (data["status"], data["mode"]) == ("answered", "stored")
    assert [s["text"] for s in data["statements"]] == [
        "CR 강제: 목표가 1.2345 EUR 초과 견적은 탈락합니다",
        "무제한 보증: 기간 상한이 없습니다",
    ]
    assert cited(data) == [("2.2", 3), ("4.5", 5)]


def test_only_verified_red_flags_are_shown(ruleset: Ruleset):
    """INV-3: an unverified summary is not an answer, and a citation that is in no clause is left out."""
    gone = make_locus(99, section_id="9.9")
    context = make_context(
        ruleset,
        analyses=[
            make_analysis(DELIVERY, label="A", reason="a", status="unverified"),
            make_analysis(QUALITY, label="B", reason="b", cited=[gone]),
            make_analysis(WARRANTY, label="C", reason="c", cited=[gone, WARRANTY.sentences[1].locus]),
        ],
    )
    data = ask(context, FLAGS)
    assert [s["text"] for s in data["statements"]] == ["C: c"] and cited(data) == [("4.5", 6)]


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_a_hidden_red_flag_leaves_no_trace(ruleset: Ruleset, group: str):
    """INV-8: no reason, no label, no count: the reply is the reply of a package without the clause."""
    full = make_context(ruleset, analyses=[PRICE_FLAG, WARRANTY_FLAG])
    without = make_context(ruleset, VISIBLE, analyses=[WARRANTY_FLAG])
    data = ask(full, FLAGS, group=group)
    assert data == ask(without, FLAGS, group=group)
    assert [s["text"] for s in data["statements"]] == ["무제한 보증: 기간 상한이 없습니다"]
    assert not [word for word in (*SECRETS, "CR 강제", "목표가 1") if word in dumped(data)]


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_only_hidden_red_flags_is_the_same_as_none(ruleset: Ruleset, group: str):
    """INV-8: "there is one you may not see" would already say something."""
    data = ask(make_context(ruleset, analyses=[PRICE_FLAG]), FLAGS, group=group)
    assert (data["status"], data["mode"]) == ("no_evidence", "stored")
    assert data == ask(make_context(ruleset), FLAGS, group=group)


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
@pytest.mark.parametrize(("question", "claim"), [(FLAGS, NO_FLAGS), (CONFLICTS, NO_CONFLICTS)])
def test_a_restricted_team_is_not_told_that_there_is_nothing(
    ruleset: Ruleset, group: str, question: str, claim: str
):
    """INV-12: "there are none" is false when they are hidden; only what may be read is spoken of."""
    context = make_context(ruleset, analyses=[PRICE_FLAG], issues=[HIDDEN_ISSUE])
    message = ask(context, question, group=group)["message"]
    assert claim not in message and message.startswith("열람할 수 있는 조항에서는")


def test_an_open_clause_citing_hidden_text_is_withheld(ruleset: Ruleset):
    """INV-8, fail closed: the finding rests on a hidden sentence, so it is hidden too."""
    leaking = make_analysis(WARRANTY, label="무제한 보증", reason="r", cited=[PRICE.sentences[0].locus])
    context = make_context(ruleset, analyses=[leaking])
    assert ask(context, FLAGS, group="cost_restricted")["status"] == "no_evidence"
    assert ask(context, FLAGS)["status"] == "answered"


def test_no_red_flags(ruleset: Ruleset):
    data = ask(make_context(ruleset, analyses=[make_analysis(WARRANTY)]), FLAGS)
    assert (data["status"], data["mode"]) == ("no_evidence", "stored") and NO_FLAGS in data["message"]


# ── conflicts ───────────────────────────────────────────────────────────────
def test_every_issue_is_listed_most_severe_first(ruleset: Ruleset):
    """INV-3, INV-12: all categories, high to low, each citing its evidence."""
    attachment = make_issue(
        "a-1", [WARRANTY.sentences[0]], category="missing_attachment", severity="low", title="첨부 없음"
    )
    notation = make_issue(
        "n-1", [QUALITY.sentences[1]], category="notation", severity="medium", title="표기가 다름"
    )
    data = ask(make_context(ruleset, issues=[attachment, notation, TERMS]), CONFLICTS)
    assert (data["status"], data["mode"]) == ("answered", "stored")
    assert [s["text"] for s in data["statements"]] == ["인도조건이 다릅니다", "표기가 다름", "첨부 없음"]
    assert cited(data)[:2] == [("2.1", 2), ("5.1", 7)]


def test_issue_evidence_outside_every_clause_is_left_out(ruleset: Ruleset):
    """INV-3: a citation that cannot be opened is not given; an issue without one is not shown."""
    nowhere = make_sentence("Nowhere.", 99, section_id="9.9")
    issues = [
        make_issue("c-1", [nowhere], title="근거 없음"),
        make_issue("c-2", [nowhere, *QUALITY.sentences]),
    ]
    data = ask(make_context(ruleset, issues=issues), CONFLICTS)
    assert len(data["statements"]) == 1 and cited(data) == [("5.1", 7), ("5.1", 8)]


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_a_hidden_issue_leaves_no_trace(ruleset: Ruleset, group: str):
    """INV-8: one hidden piece of evidence hides the whole issue, its id and its existence."""
    full = make_context(ruleset, issues=[HIDDEN_ISSUE, TERMS])
    without = make_context(ruleset, VISIBLE, issues=[TERMS])
    data = ask(full, CONFLICTS, group=group)
    assert data == ask(without, CONFLICTS, group=group)
    assert [s["text"] for s in data["statements"]] == ["인도조건이 다릅니다"]
    assert not [word for word in (*SECRETS, "target_price", "목표가 1") if word in dumped(data)]


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_an_issue_marked_cost_sensitive_is_hidden(ruleset: Ruleset, group: str):
    """INV-8: the mark counts even when every sentence is open."""
    marked = make_issue("c-x", list(WARRANTY.sentences), title="비밀 이슈", is_cost_sensitive=True)
    data = ask(make_context(ruleset, issues=[marked]), CONFLICTS, group=group)
    assert data == ask(make_context(ruleset), CONFLICTS, group=group), "as if it did not exist"
    assert "비밀 이슈" not in dumped(data)


def test_no_conflicts(ruleset: Ruleset):
    data = ask(make_context(ruleset), CONFLICTS)
    assert (data["status"], data["mode"]) == ("no_evidence", "stored") and NO_CONFLICTS in data["message"]


# ── conflicts attached to another answer ────────────────────────────────────
def test_an_answer_resting_on_a_conflicting_sentence_names_the_conflict(ruleset: Ruleset):
    """INV-3, §3.4: the other side of a contradiction is shown with the answer."""
    other = make_issue("c-other", [DELIVERY.sentences[0]])
    missing = make_issue("a-1", [WARRANTY.sentences[0]], category="missing_attachment")
    mine = make_issue("c-warranty", [WARRANTY.sentences[0], QUALITY.sentences[1]], title="보증이 다릅니다")
    data = ask(make_context(ruleset, issues=[other, missing, mine]), "보증")
    assert data["mode"] == "extractive" and ("4.5", 5) in cited(data)
    (conflict,) = data["conflicts"]
    assert (conflict["id"], conflict["title"], conflict["detail"]) == (
        "c-warranty",
        "보증이 다릅니다",
        "c-warranty 설명",
    )
    assert [c["sentence_index"] for c in conflict["citations"]] == [5, 8]


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_a_hidden_conflict_is_not_attached(ruleset: Ruleset, group: str):
    """INV-8: the open half of a hidden contradiction does not reveal it."""
    half = make_issue("c-target_price", [WARRANTY.sentences[0], PRICE.sentences[0]], title="목표가 1.2345")
    context = make_context(ruleset, issues=[half])
    data = ask(context, "보증", group=group)
    assert data["status"] == "answered" and data["conflicts"] == []
    assert "target_price" not in dumped(data) and "1.2345" not in dumped(data)
    assert [c["id"] for c in ask(context, "보증")["conflicts"]] == ["c-target_price"]
