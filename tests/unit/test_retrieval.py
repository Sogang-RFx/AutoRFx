"""Retrieval and chat: glossary expansion, the evidence gate, cited answers, and the cost filter."""

from __future__ import annotations

import json

import pytest

from autorfx.access import hidden_keys
from autorfx.analysis import Answer, RuleOnlyProvider, ScriptedProvider, TransientProviderError
from autorfx.analysis.llm import answer_prompt
from autorfx.retrieval import SearchIndex, answer_question
from autorfx.rules import Ruleset
from contract_schema import assert_valid
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
RULE_ONLY = RuleOnlyProvider()


@pytest.fixture(scope="module")
def index(ruleset: Ruleset) -> SearchIndex:
    return SearchIndex(CLAUSES, ruleset)


def ask(index: SearchIndex, question: str, *, group: str = "cost_visible", provider=RULE_ONLY) -> dict:
    data = answer_question(question, index=index, clauses=CLAUSES, group=group, provider=provider)  # type: ignore[arg-type]
    assert_valid("chat", {"data": data, "provenance": {"chat": "real"}})
    return data


def cited(data: dict) -> list[tuple[str, int]]:
    return [(c["section_id"], c["sentence_index"]) for c in data["answer"]["finding"]["citations"]]


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


# ── answers ─────────────────────────────────────────────────────────────────
def test_without_an_llm_the_answer_is_the_original_sentences(index: SearchIndex):
    """RAG-03, INV-12: extractive mode quotes; it says so and cites every sentence it shows."""
    data = ask(index, "What is the warranty period?")
    assert (data["status"], data["mode"]) == ("answered", "extractive")
    answer = data["answer"]
    assert answer["citation_status"] == "verified" and answer["finding"]["kind"] == "answer"
    assert answer["finding"]["text"].splitlines()[0] == WARRANTY.sentences[0].text
    assert cited(data)[0] == ("4.5", 5) and "LLM이 연결되지 않아" in data["message"]
    assert data["message"].endswith("문서에 없습니다: period."), "a partial match is said to be one"
    assert "문서에 없습니다" not in ask(index, "보증")["message"]


def test_no_evidence_gives_no_answer_and_offers_topics(index: SearchIndex):
    """RAG-03, INV-3: nothing is answered without evidence; the topics offered exist in the text."""
    data = ask(index, "Who is the CEO?")
    assert (data["status"], data["answer"]) == ("no_evidence", None)
    assert data["suggestions"][:2] == ["납기", "인도조건"] and "보증" in data["suggestions"]


def test_empty_or_oversized_question_is_refused(index: SearchIndex):
    with pytest.raises(ValueError, match="질문을 입력"):
        ask(index, "   ")
    with pytest.raises(ValueError, match="500자"):
        ask(index, "x" * 501)


def test_llm_answer_is_accepted_when_its_citations_and_numbers_check_out(index: SearchIndex):
    """CITE-01: the model cites sentence numbers; code turns them into positions and verifies them."""
    provider = ScriptedProvider(
        answers=[Answer(text="보증 기간은 36개월입니다.", citations=(1,), answered=True)]
    )
    data = ask(index, "보증 기간은?", provider=provider)
    assert (data["status"], data["mode"]) == ("answered", "generative")
    assert cited(data) == [("4.5", 5)] and data["answer"]["finding"]["text"] == "보증 기간은 36개월입니다."
    assert len(provider.answer_calls) == 1 and provider.answer_calls[0][2] is None


def test_llm_answer_with_an_unsupported_number_is_retried_then_replaced(index: SearchIndex):
    """CITE-03, INV-3: '24' is not in the cited sentence; after one retry the original sentences are shown."""
    wrong = Answer(text="보증 기간은 24개월입니다.", citations=(1,), answered=True)
    provider = ScriptedProvider(answers=[wrong, wrong])
    data = ask(index, "보증 기간은?", provider=provider)
    assert (data["status"], data["mode"]) == ("answered", "extractive")
    assert "24" not in data["answer"]["finding"]["text"] and "검증을 통과하지 못해" in data["message"]
    assert [call[2] is None for call in provider.answer_calls] == [True, False], "the retry carries feedback"


def test_llm_retry_can_succeed(index: SearchIndex):
    provider = ScriptedProvider(
        answers=[
            Answer(text="24개월", citations=(1,), answered=True),
            Answer(text="36개월입니다.", citations=(1,), answered=True),
        ]
    )
    data = ask(index, "보증 기간은?", provider=provider)
    assert (data["mode"], data["answer"]["finding"]["text"]) == ("generative", "36개월입니다.")


@pytest.mark.parametrize(
    "reply",
    [
        Answer(text="36개월", citations=(99,), answered=True),
        Answer(text="36개월", citations=(), answered=True),
        Answer(text="  ", citations=(1,), answered=True),
    ],
)
def test_llm_answer_that_cites_nothing_usable_is_replaced(index: SearchIndex, reply: Answer):
    """CITE-01: a sentence number that was not given, no citation, or no text is never shown."""
    data = ask(index, "보증 기간은?", provider=ScriptedProvider(answers=[reply]))
    assert data["mode"] == "extractive" and data["answer"]["citation_status"] == "verified"


def test_llm_that_finds_no_answer_in_the_evidence_gives_no_evidence(index: SearchIndex):
    """RAG-03, INV-3: when the model says the evidence does not answer, nothing is shown as an answer.

    Not as a generative answer (a "no information" sentence with citations would pass the citation
    check), and not as quoted sentences either: the model has just judged them not to answer.
    """
    no_answer = Answer(text="제공된 문장에는 그 정보가 없습니다.", citations=(1,), answered=False)
    provider = ScriptedProvider(answers=[no_answer])
    data = ask(index, "보증 기간은?", provider=provider)
    assert (data["status"], data["mode"], data["answer"]) == ("no_evidence", "generative", None)
    assert data["suggestions"], "the topics the text does cover are offered"
    assert len(provider.answer_calls) == 1, "a judged non-answer is not retried"


def test_llm_that_finds_no_answer_on_the_retry_gives_no_evidence(index: SearchIndex):
    """RAG-03: a non-answer after a rejected first reply is still no_evidence, not quoted sentences."""
    wrong = Answer(text="보증 기간은 24개월입니다.", citations=(1,), answered=True)
    no_answer = Answer(text="제공된 문장에는 그 정보가 없습니다.", citations=(), answered=False)
    provider = ScriptedProvider(answers=[wrong, no_answer])
    data = ask(index, "보증 기간은?", provider=provider)
    assert (data["status"], data["mode"], data["answer"]) == ("no_evidence", "generative", None)
    assert len(provider.answer_calls) == 2


def test_answer_prompt_asks_the_model_to_say_when_nothing_answers():
    """RAG-03: the model reports a non-answer as a flag, not only in prose."""
    prompt = answer_prompt("보증 기간은?", [], None)
    assert "answered" in prompt and "false" in prompt


def test_provider_outage_falls_back_to_the_original_sentences(index: SearchIndex):
    """ENGINE-02: a timeout does not fail the request."""
    provider = ScriptedProvider(answers=[TransientProviderError("timeout")])
    data = ask(index, "보증 기간은?", provider=provider)
    assert (data["status"], data["mode"]) == ("answered", "extractive") and len(provider.answer_calls) == 1


# ── cost filter ─────────────────────────────────────────────────────────────
def test_cost_visible_team_gets_the_price(index: SearchIndex):
    data = ask(index, "목표가는 얼마인가요?")
    assert data["status"] == "answered" and SECRET in data["answer"]["finding"]["text"]
    assert cited(data)[0] == ("2.2", 3)


def test_restricted_team_is_told_the_topic_is_hidden_and_nothing_else(index: SearchIndex):
    """INV-4, INV-8: a cost question is refused before retrieval; the topics offered are open ones."""
    data = ask(index, "목표가는 얼마인가요?", group="cost_restricted")
    assert (data["status"], data["answer"]) == ("restricted", None)
    assert data["suggestions"] == ["납기", "인도조건", "보증", "인증"]
    assert index.suggestions("cost_visible") == ["납기", "인도조건", "보증", "목표가"]
    assert ask(index, "납기와 결제 조건은?", group="cost_restricted")["status"] == "restricted"
    for secret in (SECRET, "1.2345", "target price", "Zielvorgabe", "rejected"):
        assert secret not in json.dumps(data, ensure_ascii=False)


def test_no_question_reaches_hidden_text_for_a_restricted_team(index: SearchIndex):
    """INV-4: whatever is asked, hits and citations stay outside the cost-sensitive clause."""
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
        data = ask(index, question, group="cost_restricted")
        assert SECRET not in json.dumps(data, ensure_ascii=False), question
    assert "목표가" not in index.suggestions("cost_restricted")
    assert index.search("target price", group="cost_visible").hits[0].clause_id == "d01:2.2"


def test_unknown_group_reads_the_reduced_index(index: SearchIndex):
    """INV-8, fail closed: a group value nobody defined is treated as restricted."""
    assert not index.search("target price", group="auditors").hits  # type: ignore[arg-type]
    assert ask(index, "target price", group="auditors")["status"] == "restricted"


def test_restricted_team_still_gets_answers_about_open_clauses(index: SearchIndex):
    data = ask(index, "인도조건은?", group="cost_restricted")
    assert data["status"] == "answered" and "FCA" in data["answer"]["finding"]["text"]
    assert not index.is_cost_question("인도조건은?") and index.is_cost_question("결제 조건은?")
