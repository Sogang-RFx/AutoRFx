"""Gathering evidence (RAG-03, RAG-04, INV-4, INV-7, docs/design/chat.md §3.3)."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pytest

from autorfx.access import hidden_keys
from autorfx.chat.gather import MAX_CHARS, gather
from autorfx.chat.models import ChatContext, Evidence
from autorfx.models import Clause, ParsedSentence
from autorfx.retrieval.dense import DenseIndex
from autorfx.rules import Ruleset
from chat_helpers import CLAUSES, PRICE, make_context
from factories import make_clause, make_file, make_issue, make_sentence, with_heading
from topic_encoder import TopicEncoder

PAD = "General background of the programme is described here in plain words without any particular value."
RESTRICTED_GROUPS = ["cost_restricted", "auditors"]


def filler(number: int, *, source_doc: str = "d01", sentences: int = 10) -> Clause:
    """Text no test question asks about; every clause stands on a page of its own."""
    return make_clause(
        f"{number}.1",
        [f"{PAD} Item {number} of the list, line {i}." for i in range(1, sentences + 1)],
        source_doc=source_doc,
        page_number=number,
    )


def large(*special: Clause) -> tuple[Clause, ...]:
    """A package well over the budget: thirty filler clauses, then the clauses a test is about."""
    clauses = (*(filler(n, sentences=15) for n in range(1, 31)), *special)
    assert sum(len(c.text) for c in clauses) > 2 * MAX_CHARS
    return clauses


def get(
    context: ChatContext,
    question: str,
    queries: Sequence[str] = (),
    *,
    group: str = "cost_visible",
    overview: bool = False,
) -> tuple[Evidence, ...]:
    """``gather`` plus what must hold for every result: numbered from 1, in document order, no repeats."""
    found = gather(question, queries, context=context, group=group, overview=overview)  # type: ignore[arg-type]
    order = {s.locus.key(): i for i, s in enumerate(s for c in context.clauses for s in c.sentences)}
    positions = [order[e.locus.key()] for e in found]
    assert positions == sorted(set(positions))
    assert [e.number for e in found] == list(range(1, len(found) + 1))
    if group != "cost_visible":
        assert not hidden_keys(context.clauses) & keys(found), "INV-4"
    return found


def keys(found: Sequence[Evidence | ParsedSentence]) -> set[tuple[str, int, int]]:
    return {e.locus.key() for e in found}


def size(found: Sequence[Evidence]) -> int:
    return sum(len(e.text) for e in found)


WARRANTY = make_clause(
    "40.1",
    ["The warranty covers all parts.", "Defects are reported in writing.", PAD],
    page_number=40,
)
SECRET = make_clause(
    "41.1",
    ["The warranty deduction is 1.2345 EUR per piece.", "Warranty quotes above it are rejected."],
    page_number=41,
    is_cost_sensitive=True,
)


# ── small package ───────────────────────────────────────────────────────────
def test_a_small_package_is_given_whole_and_in_document_order(ruleset: Ruleset):
    """RAG-03: nothing is searched, so nothing can be missed, even for a question no word matches."""
    found = get(make_context(ruleset), "대표이사가 누구인가요?")
    assert [e.text for e in found] == [s.text for c in CLAUSES for s in c.sentences]
    assert found[0].locus == CLAUSES[0].sentences[0].locus


def test_evidence_names_its_file_and_the_clauses_own_title(ruleset: Ruleset):
    """§3.6: the prompt groups evidence by file and clause title."""
    clause = with_heading(make_clause("4.5", ["4.5 Warranty", "The warranty covers all parts."]))
    context = make_context(ruleset, [clause], files=[make_file("d01", "specification.pdf")])
    assert {(e.file, e.title) for e in get(context, "warranty")} == {("specification.pdf", "4.5 Warranty")}


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_an_inherited_price_title_is_not_given(ruleset: Ruleset, group: str):
    """INV-8: an open clause under "Target price EUR 1.20" must not carry that title to the model."""
    open_clause = make_clause("4.2.1", ["Delivery is FCA supplier plant."], title="Target price EUR 1.20")
    found = get(make_context(ruleset, [open_clause]), "delivery", group=group)
    assert found and all("1.20" not in e.title and "price" not in e.title.casefold() for e in found)


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_only_visible_text_decides_whether_a_package_is_small(ruleset: Ruleset, group: str):
    """INV-4: a huge hidden clause neither appears nor changes what a restricted team gets."""
    hidden = make_clause("9.1", [PAD * 4] * 60, page_number=9, is_cost_sensitive=True)
    clauses = [c for c in CLAUSES if c is not PRICE]
    found = get(make_context(ruleset, [*clauses, hidden]), "warranty", group=group)
    assert found == get(make_context(ruleset, clauses), "warranty", group=group)
    assert len(found) == sum(len(c.sentences) for c in clauses)


# ── large package ───────────────────────────────────────────────────────────
def test_a_matching_short_clause_is_given_whole_and_nothing_else(ruleset: Ruleset):
    """RAG-03: small-to-big: the sentence is found, its clause is given."""
    found = get(make_context(ruleset, large(WARRANTY)), "warranty")
    assert [e.text for e in found] == [s.text for s in WARRANTY.sentences]


def test_a_large_package_without_a_match_gives_nothing(ruleset: Ruleset):
    """RAG-03: filler is not evidence."""
    assert get(make_context(ruleset, large(WARRANTY)), "대표이사가 누구인가요?") == ()


def test_the_queries_of_the_plan_are_searched_too(ruleset: Ruleset):
    """RAG-03: a Korean word outside the glossary finds nothing; its translation does."""
    context = make_context(ruleset, large(WARRANTY))
    assert get(context, "고장나면 어떻게 되나요?") == ()
    assert keys(get(context, "고장나면 어떻게 되나요?", ("warranty",))) == keys(WARRANTY.sentences)


def test_the_budget_limits_evidence_and_the_best_clause_is_in_it(ruleset: Ruleset):
    """RAG-03: forty clauses match; the one that states both things is last and is still included."""
    many = [
        make_clause(
            f"{n}.1", [f"The warranty rule {n} applies.", *[f"{PAD} {i}" for i in range(9)]], page_number=n
        )
        for n in range(50, 90)
    ]
    best = make_clause(
        "95.1", ["Warranty and recall are handled together.", PAD, f"{PAD} End."], page_number=95
    )
    found = get(make_context(ruleset, large(*many, best)), "warranty recall")
    assert MAX_CHARS // 2 < size(found) <= MAX_CHARS
    assert keys(best.sentences) <= keys(found)


def test_a_best_clause_over_the_budget_is_cut_around_its_match(ruleset: Ruleset):
    """RAG-03: the best clause is always there; cutting it must not cut away the sentence that matched."""
    long = " ".join([PAD] * 25)
    texts = [f"{long} Part {i}." for i in range(12)]
    texts[10] = f"The warranty covers all parts. {long}"
    clause = make_clause("60.1", texts, page_number=60)
    assert len(clause.text) > MAX_CHARS
    found = get(make_context(ruleset, [clause]), "warranty")
    assert found and size(found) <= MAX_CHARS
    assert clause.sentences[10].locus.key() in keys(found)


@pytest.mark.parametrize(
    ("hit", "expected"),
    [(20, {0, 17, 18, 19, 20, 21, 22, 23}), (1, {0, 1, 2, 3, 4}), (39, {0, 36, 37, 38, 39})],
)
def test_a_long_clause_gives_the_neighbours_of_the_match_and_its_heading(
    ruleset: Ruleset, hit: int, expected: set[int]
):
    """RAG-03: three sentences either side, never across the clause boundary."""
    texts = ["70.1 General conditions", *[f"{PAD} Row {i}." for i in range(1, 40)]]
    texts[hit] = "The insurance is paid by the supplier."
    clause = with_heading(make_clause("70.1", texts, page_number=70))
    after = filler(71, sentences=3)
    found = get(make_context(ruleset, large(clause, after)), "insurance")
    assert keys(found) == {clause.sentences[i].locus.key() for i in expected}


def test_gathering_is_deterministic(ruleset: Ruleset):
    """INV-7: the same question over the same package gives the same evidence."""
    clauses = large(WARRANTY, SECRET)
    first = get(make_context(ruleset, clauses), "warranty", ("defects",))
    assert first == get(make_context(ruleset, clauses), "warranty", ("defects",))


# ── dense ───────────────────────────────────────────────────────────────────
DEADLINE = make_clause("42.1", ["Quotations must be received by 15 December 2026."], page_number=42)
PARAPHRASE = "견적서를 언제까지 내야 하나요?"


def test_the_similarity_gate_does_not_hide_a_paraphrase(ruleset: Ruleset):
    """RAG-03: a fixed threshold refused right sentences before the model saw them (§1)."""
    clauses = large(DEADLINE)
    context = make_context(ruleset, clauses, dense=DenseIndex(clauses, TopicEncoder(), min_score=2.0))
    assert not context.index.search(PARAPHRASE, group="cost_visible").passed
    assert DEADLINE.sentences[0].locus.key() in keys(get(context, PARAPHRASE))


class BrokenDense:
    def search(self, *args: object, **kwargs: object) -> tuple:
        raise RuntimeError("embedding service down")


def test_a_dense_failure_falls_back_to_bm25(ruleset: Ruleset, caplog: pytest.LogCaptureFixture):
    """INV-12: an embedding outage costs recall, not the answer, and is logged."""
    caplog.set_level(logging.INFO)
    clauses = large(WARRANTY)
    broken = make_context(ruleset, clauses, dense=BrokenDense())  # type: ignore[arg-type]
    assert get(broken, "warranty") == get(make_context(ruleset, clauses), "warranty") != ()
    assert caplog.records and "warranty" not in caplog.text, "logged without the question"


# ── cost filter ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
@pytest.mark.parametrize("with_dense", [False, True])
def test_hidden_clauses_are_neither_scored_nor_given(ruleset: Ruleset, group: str, with_dense: bool):
    """INV-4, RAG-04: for a restricted or unknown group the hidden clause might as well not exist."""
    clauses, open_only = large(WARRANTY, SECRET), large(WARRANTY)

    def context(of: Sequence[Clause]) -> ChatContext:
        return make_context(ruleset, of, dense=DenseIndex(of, TopicEncoder()) if with_dense else None)

    queries = ("warranty", "1.2345 EUR per piece", "rejected quotes")
    found = get(context(clauses), "warranty deduction", queries, group=group)
    assert found == get(context(open_only), "warranty deduction", queries, group=group)
    assert all("1.2345" not in e.text for e in found)
    assert keys(SECRET.sentences) <= keys(get(context(clauses), "warranty deduction", queries))


# ── conflict expansion ──────────────────────────────────────────────────────
def test_the_other_side_of_a_conflict_is_added(ruleset: Ruleset):
    """§3.3: the answer must be able to state both values."""
    clauses = large(WARRANTY)
    other = clauses[4].sentences[6]
    nowhere = make_sentence("In no clause.", 99, page_number=99)
    issue = make_issue("c-1", [WARRANTY.sentences[0], other, nowhere])
    found = get(make_context(ruleset, clauses, issues=[issue]), "warranty")
    assert keys(found) == keys(WARRANTY.sentences) | {other.locus.key()}


def test_only_conflicts_of_gathered_sentences_are_added(ruleset: Ruleset):
    clauses = large(WARRANTY)
    issues = [
        make_issue("c-far", [clauses[2].sentences[0], clauses[3].sentences[0]]),
        make_issue("n-1", [WARRANTY.sentences[0], clauses[4].sentences[0]], category="notation"),
    ]
    found = get(make_context(ruleset, clauses, issues=issues), "warranty")
    assert keys(found) == keys(WARRANTY.sentences)


def test_conflict_expansion_ignores_the_budget(ruleset: Ruleset):
    """§3.3: a full budget does not leave a contradiction half shown."""
    long = " ".join([PAD] * 25)
    texts = ["The warranty covers all parts.", *[f"{long} Part {i}." for i in range(11)]]
    clause = make_clause("60.1", texts, page_number=60)
    other = make_clause("61.1", [f"{long} {long}"], page_number=61)
    issue = make_issue("c-1", [clause.sentences[0], other.sentences[0]])
    found = get(make_context(ruleset, [clause, other], issues=[issue]), "warranty")
    assert other.sentences[0].locus.key() in keys(found)


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_conflict_expansion_never_reaches_hidden_text(ruleset: Ruleset, group: str):
    """INV-4, INV-8: neither through hidden evidence nor through an issue marked cost-sensitive."""
    clauses = large(WARRANTY, SECRET)
    marked_side = clauses[4].sentences[0]
    issues = [
        make_issue("c-price", [WARRANTY.sentences[0], SECRET.sentences[0]]),
        make_issue("c-marked", [WARRANTY.sentences[0], marked_side], is_cost_sensitive=True),
    ]
    found = get(make_context(ruleset, clauses, issues=issues), "warranty", group=group)
    assert keys(found) == keys(WARRANTY.sentences)
    full = keys(get(make_context(ruleset, clauses, issues=issues), "warranty"))
    assert {SECRET.sentences[0].locus.key(), marked_side.locus.key()} <= full


# ── overview ────────────────────────────────────────────────────────────────
def overview_package() -> tuple[Clause, ...]:
    front = make_clause("front", ["Request for quotation."], page_number=45, kind="front_matter")
    second = tuple(filler(n, source_doc="d02") for n in range(1, 11))
    return (*large(front), *second)


def test_an_overview_includes_the_start_of_every_file(ruleset: Ruleset):
    """§3.3: whatever the question matches, each file is represented by its first clause and front matter."""
    clauses = overview_package()
    context = make_context(ruleset, clauses)
    found = keys(get(context, "대표이사", overview=True))
    for expected in (clauses[0], clauses[30], clauses[31]):
        assert keys(expected.sentences) <= found, expected.clause_id
    assert get(context, "대표이사") == ()


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_an_overview_does_not_open_a_hidden_first_clause(ruleset: Ruleset, group: str):
    """INV-4: a price sheet at the front of a file stays hidden (checked in ``get``)."""
    clauses = overview_package()
    hidden = clauses[31].model_copy(update={"is_cost_sensitive": True})
    context = make_context(ruleset, (*clauses[:31], hidden, *clauses[32:]))
    assert get(context, "RFQ 요약", overview=True, group=group)


# ── the whole budget, and contradictions ────────────────────────────────────
def test_a_clause_that_does_not_fit_does_not_end_the_search(ruleset: Ruleset):
    """RAG-03: a package used 4,809 of 16,000 characters and its deadline, one clause further, was lost."""
    long = " ".join([PAD] * 8)
    bulky = [
        make_clause(f"{n}.1", [f"Warranty and recall rule {n}. {long}" for _ in range(12)], page_number=n)
        for n in range(50, 53)
    ]
    last = make_clause("96.1", ["A warranty is agreed in writing."], page_number=96)
    found = get(make_context(ruleset, large(*bulky, last)), "warranty recall")
    assert size(found) > 0.9 * MAX_CHARS and size(found) <= MAX_CHARS
    assert keys(last.sentences) <= keys(found)


def test_evidence_names_the_sentences_it_contradicts(ruleset: Ruleset):
    """The writer must see that two sentences disagree, or it picks one of them."""
    a = make_clause("1.1", ["Delivery is FCA."], source_doc="d01")
    b = make_clause("2.1", ["Delivery is DDP.", "Two deliveries a week."], source_doc="d02")
    issue = make_issue("c-term", [a.sentences[0], b.sentences[0]])
    other = make_issue("m-1", [a.sentences[0], b.sentences[1]], category="missing_attachment")
    found = get(make_context(ruleset, [a, b], issues=[issue, other]), "delivery")
    assert [e.contradicts for e in found] == [(2,), (1,), ()]


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_a_hidden_contradiction_is_not_mentioned(ruleset: Ruleset, group: str):
    """INV-8: that a visible sentence contradicts a hidden one says something about the hidden one."""
    issue = make_issue("c-x", [PRICE.sentences[0], CLAUSES[0].sentences[0]])
    found = get(make_context(ruleset, issues=[issue]), "delivery", group=group)
    assert found and all(e.contradicts == () for e in found)
