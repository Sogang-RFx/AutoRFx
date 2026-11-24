"""Retrieval: glossary expansion, the evidence gate, and the cost filter."""

from __future__ import annotations

import pytest

from autorfx.access import hidden_keys
from autorfx.retrieval import SearchIndex
from autorfx.rules import Ruleset
from factories import make_clause, make_sentence

SECRET = "1.2345 EUR"
DELIVERY = make_clause(
    "2.1",
    [
        "Die Anlieferung erfolgt täglich im Lieferabruf.",
        "Delivery is FCA supplier plant according to Incoterms 2020.",
    ],
    title="Lieferung",
    lang="de",
)
PRICE = make_clause(
    "2.2",
    [f"The target price is {SECRET} per piece.", "Quotes above the target price will be rejected."],
    title="Zielvorgabe",
    first_sentence=3,
    is_cost_sensitive=True,
)
WARRANTY = make_clause(
    "4.5",
    [
        "Die Gewährleistungsfrist beträgt 36 Monate ab Fahrzeugerstzulassung.",
        "Defective parts are replaced free of charge.",
    ],
    title="Gewährleistung",
    first_sentence=5,
    lang="de",
)
QUALITY = make_clause(
    "5.1",
    [
        "The supplier shall hold a valid IATF 16949 certification.",
        "Process capability shall be Cpk 1.67 or better.",
    ],
    title="Quality",
    first_sentence=7,
)
CLAUSES = (DELIVERY, PRICE, WARRANTY, QUALITY)


@pytest.fixture(scope="module")
def index(ruleset: Ruleset) -> SearchIndex:
    return SearchIndex(CLAUSES, ruleset)


# ── retrieval ───────────────────────────────────────────────────────────────
def test_korean_question_finds_german_and_english_text(index: SearchIndex):
    """RAG-02: the glossary turns '납기' into Lieferung, Anlieferung, delivery; no model is involved."""
    result = index.search("납기는 어떻게 되나요?", group="cost_visible")
    assert result.passed and result.concepts == ("납기",)
    assert {hit.clause_id for hit in result.hits} == {"d01:2.1"}
    assert [hit.rank for hit in result.hits] == [1, 2]


def test_compound_and_inflected_words_match(index: SearchIndex):
    """RAG-01: 'Gewährleistung' covers 'Gewährleistungsfrist'; 'certifications' covers 'certification'."""
    assert (
        index.search("보증 기간", group="cost_visible")
        .hits[0]
        .sentence.text.startswith("Die Gewährleistungsfrist")
    )
    assert index.search("certifications", group="cost_visible").hits[0].clause_id == "d01:5.1"
    assert not index.search("party", group="cost_visible").passed, "'party' is not an inflection of 'parts'"


def test_a_question_about_something_the_rfq_does_not_say_has_no_evidence(index: SearchIndex):
    """RAG-03: words the text never uses, or stop words, are not evidence."""
    result = index.search("Who is the CEO?", group="cost_visible")
    assert not result.passed and result.missing == ("ceo",) and result.hits == ()
    assert not index.search("대표이사가 누구인가요?", group="cost_visible").passed
    assert not index.search("what is the", group="cost_visible").passed, "stop words alone ask nothing"


def test_gate_wants_a_domain_term_or_two_things_in_one_clause(index: SearchIndex):
    """RAG-03: one ordinary word shared with the text is not evidence when the rest is absent from it."""
    ceo = index.search("Who is the CEO of the supplier?", group="cost_visible")
    assert not ceo.passed and ceo.missing == ("ceo",), "only 'supplier' is in the text"
    assert index.search("supplier", group="cost_visible").passed, "(c) everything asked about is stated"
    two = index.search("IATF supplier weather", group="cost_visible")
    assert two.passed and {hit.clause_id for hit in two.hits} == {"d01:5.1"}, "(b) two things in one clause"
    assert two.missing == ("weather",)
    domain = index.search("warranty for spaceships", group="cost_visible")
    assert domain.passed and domain.hits[0].clause_id == "d01:4.5", "(a) a domain term"
    assert domain.missing == ("spaceships",)
    topic = index.search("supplier warranty", group="cost_visible")
    assert {hit.clause_id for hit in topic.hits} == {"d01:4.5"}, (
        "with a topic named, 'supplier' decides nothing"
    )


def test_the_longest_wording_wins(index: SearchIndex):
    """RAG-02: '단가 인하' is the price reduction, not the price as well; the same holds for English."""
    assert [c.label for c in index.concepts("단가 인하 조건은?", keep_hangul=False)] == ["단가 인하"]
    assert [c.label for c in index.concepts("the annual price reduction")] == ["annual price reduction"]
    assert [c.label for c in index.concepts("prices")] == ["prices"], "the label is what was typed"
    assert [c.label for c in index.concepts("die Gewährleistung")] == ["gewährleistung"], "'die' is no tool"


def test_synonyms_count_once_and_the_specific_term_wins(index: SearchIndex):
    """RAG-02: '목표가' asks for the target price, not for every sentence that says 'price'."""
    assert index.search("목표가", group="cost_visible").hits[0].sentence.text.startswith("The target price")
    assert index.search("가격", group="cost_visible").hits[0].clause_id == "d01:2.2"


def test_an_inherited_chapter_title_is_not_indexed(ruleset: Ruleset):
    """RAG-01: a chapter title ('Prices and terms') does not make the delivery clause about price."""
    delivery = make_clause("4.1", ["4.1 Basis: FCA supplier plant, packed."], title="Prices and terms")
    assert not SearchIndex([delivery], ruleset).search("가격", group="cost_visible").passed
    heading = make_sentence("4 Prices and terms", 1, role="heading", section_id="4")
    own = delivery.model_copy(update={"sentences": (heading, *delivery.sentences)})
    assert SearchIndex([own], ruleset).search("가격", group="cost_visible").passed


def test_search_is_deterministic(index: SearchIndex):
    """INV-7: the same question gives the same hits in the same order."""
    first = index.search("warranty and delivery", group="cost_visible")
    assert first == index.search("warranty and delivery", group="cost_visible")


def test_headings_are_not_answers(ruleset: Ruleset):
    """RAG-01: a heading names the clause; the sentence below it is the answer."""
    heading = make_sentence("4.5 Warranty", 1, role="heading", section_id="4.5")
    clause = WARRANTY.model_copy(update={"title": "Warranty", "sentences": (heading, *WARRANTY.sentences)})
    hits = SearchIndex([clause], ruleset).search("warranty", group="cost_visible").hits
    assert hits and all(hit.sentence.role != "heading" for hit in hits)


# ── cost filter ─────────────────────────────────────────────────────────────
def test_suggestions_are_topics_the_team_can_ask_about(index: SearchIndex):
    """INV-8: a restricted team is offered open topics only."""
    assert index.suggestions("cost_restricted") == ["납기", "인도조건", "보증", "인증"]
    assert index.suggestions("cost_visible") == ["납기", "인도조건", "보증", "목표가"]
    assert "목표가" not in index.suggestions("auditors")  # type: ignore[arg-type]


def test_no_question_reaches_hidden_text_for_a_restricted_team(index: SearchIndex):
    """INV-4: whatever is asked, hits stay outside the cost-sensitive clause."""
    hidden = hidden_keys(CLAUSES)
    questions = [
        "price",
        "target price",
        "rejected quotes",
        "Zielvorgabe",
        "1.2345",
        "목표가",
        "단가",
        "delivery price",
    ]
    for question in questions:
        hits = index.search(question, group="cost_restricted").hits
        assert not hidden & {hit.sentence.locus.key() for hit in hits}, question
    assert index.search("target price", group="cost_visible").hits[0].clause_id == "d01:2.2"


def test_unknown_group_reads_the_reduced_index(index: SearchIndex):
    """INV-8, fail closed: a group value nobody defined is treated as restricted."""
    assert not index.search("target price", group="auditors").hits  # type: ignore[arg-type]


def test_a_cost_question_is_recognised_from_its_wording(index: SearchIndex):
    """RAG-04: decided before any document text is consulted."""
    assert not index.is_cost_question("인도조건은?") and index.is_cost_question("결제 조건은?")
    assert index.is_cost_question("목표가는 얼마인가요?") and index.is_cost_question("납기와 결제 조건은?")
