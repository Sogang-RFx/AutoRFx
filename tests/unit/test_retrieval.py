"""Retrieval and chat: glossary expansion, the evidence gate, cited answers, and the scope filter.

``search(..., group=...)`` is the unscoped index, which only the command-line comparison tools use.
A chat question is answered from ``SearchIndex.scoped(visible clause ids)`` (docs/auth-rbac.md §4).
"""

from __future__ import annotations

import json

import pytest

from auth_helpers import cost_restricted_scope, scope_for, visible_of
from autorfx.access import hidden_keys
from autorfx.analysis import Answer, RuleOnlyProvider, ScriptedProvider, TransientProviderError, analyze
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


@pytest.fixture(scope="module")
def ask(index: SearchIndex, ruleset: Ruleset):
    """Ask as a scope: ``full``, ``no_cost`` (every department, no cost clauses) or a department."""
    analyses = analyze(CLAUSES, ruleset, RULE_ONLY)

    def _ask(question: str, *, who: str = "full", provider=RULE_ONLY) -> dict:
        scope = scope_of(who, ruleset)
        visible = visible_of(CLAUSES, analyses, scope, ruleset)
        scoped = index.scoped(frozenset(c.clause_id for c in visible))
        data = answer_question(question, index=scoped, clauses=visible, scope=scope, provider=provider)
        assert_valid("chat", {"data": data, "provenance": {"chat": "real"}})
        return data

    return _ask


def scope_of(who: str, ruleset: Ruleset):
    if who == "full":
        return scope_for("admin", ruleset)
    return cost_restricted_scope(ruleset) if who == "no_cost" else scope_for(who, ruleset)


ALL_IDS = frozenset(c.clause_id for c in CLAUSES)
OPEN_IDS = ALL_IDS - {PRICE.clause_id}


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
def test_without_an_llm_the_answer_is_the_original_sentences(ask):
    """RAG-03, INV-12: extractive mode quotes; it says so and cites every sentence it shows."""
    data = ask("What is the warranty period?")
    assert (data["status"], data["mode"]) == ("answered", "extractive")
    answer = data["answer"]
    assert answer["citation_status"] == "verified" and answer["finding"]["kind"] == "answer"
    assert answer["finding"]["text"].splitlines()[0] == WARRANTY.sentences[0].text
    assert cited(data)[0] == ("4.5", 5) and "LLM이 연결되지 않아" in data["message"]
    assert data["message"].endswith("문서에 없습니다: period."), "a partial match is said to be one"
    assert "문서에 없습니다" not in ask("보증")["message"]


def test_no_evidence_gives_no_answer_and_offers_topics(ask):
    """RAG-03, INV-3: nothing is answered without evidence; the topics offered exist in the text."""
    data = ask("Who is the CEO?")
    assert (data["status"], data["answer"]) == ("no_evidence", None)
    assert data["suggestions"][:2] == ["납기", "인도조건"] and "보증" in data["suggestions"]


def test_empty_or_oversized_question_is_refused(ask):
    with pytest.raises(ValueError, match="질문을 입력"):
        ask("   ")
    with pytest.raises(ValueError, match="500자"):
        ask("x" * 501)


def test_llm_answer_is_accepted_when_its_citations_and_numbers_check_out(ask):
    """CITE-01: the model cites sentence numbers; code turns them into positions and verifies them."""
    provider = ScriptedProvider(
        answers=[Answer(text="보증 기간은 36개월입니다.", citations=(1,), answered=True)]
    )
    data = ask("보증 기간은?", provider=provider)
    assert (data["status"], data["mode"]) == ("answered", "generative")
    assert cited(data) == [("4.5", 5)] and data["answer"]["finding"]["text"] == "보증 기간은 36개월입니다."
    assert len(provider.answer_calls) == 1 and provider.answer_calls[0][2] is None


def test_llm_answer_with_an_unsupported_number_is_retried_then_replaced(ask):
    """CITE-03, INV-3: '24' is not in the cited sentence; after one retry the original sentences are shown."""
    wrong = Answer(text="보증 기간은 24개월입니다.", citations=(1,), answered=True)
    provider = ScriptedProvider(answers=[wrong, wrong])
    data = ask("보증 기간은?", provider=provider)
    assert (data["status"], data["mode"]) == ("answered", "extractive")
    assert "24" not in data["answer"]["finding"]["text"] and "검증을 통과하지 못해" in data["message"]
    assert [call[2] is None for call in provider.answer_calls] == [True, False], "the retry carries feedback"


def test_llm_retry_can_succeed(ask):
    provider = ScriptedProvider(
        answers=[
            Answer(text="24개월", citations=(1,), answered=True),
            Answer(text="36개월입니다.", citations=(1,), answered=True),
        ]
    )
    data = ask("보증 기간은?", provider=provider)
    assert (data["mode"], data["answer"]["finding"]["text"]) == ("generative", "36개월입니다.")


@pytest.mark.parametrize(
    "reply",
    [
        Answer(text="36개월", citations=(99,), answered=True),
        Answer(text="36개월", citations=(), answered=True),
        Answer(text="  ", citations=(1,), answered=True),
    ],
)
def test_llm_answer_that_cites_nothing_usable_is_replaced(ask, reply: Answer):
    """CITE-01: a sentence number that was not given, no citation, or no text is never shown."""
    data = ask("보증 기간은?", provider=ScriptedProvider(answers=[reply]))
    assert data["mode"] == "extractive" and data["answer"]["citation_status"] == "verified"


def test_llm_that_finds_no_answer_in_the_evidence_gives_no_evidence(ask):
    """RAG-03, INV-3: when the model says the evidence does not answer, nothing is shown as an answer.

    Not as a generative answer (a "no information" sentence with citations would pass the citation
    check), and not as quoted sentences either: the model has just judged them not to answer.
    """
    no_answer = Answer(text="제공된 문장에는 그 정보가 없습니다.", citations=(1,), answered=False)
    provider = ScriptedProvider(answers=[no_answer])
    data = ask("보증 기간은?", provider=provider)
    assert (data["status"], data["mode"], data["answer"]) == ("no_evidence", "generative", None)
    assert data["suggestions"], "the topics the text does cover are offered"
    assert len(provider.answer_calls) == 1, "a judged non-answer is not retried"


def test_llm_that_finds_no_answer_on_the_retry_gives_no_evidence(ask):
    """RAG-03: a non-answer after a rejected first reply is still no_evidence, not quoted sentences."""
    wrong = Answer(text="보증 기간은 24개월입니다.", citations=(1,), answered=True)
    no_answer = Answer(text="제공된 문장에는 그 정보가 없습니다.", citations=(), answered=False)
    provider = ScriptedProvider(answers=[wrong, no_answer])
    data = ask("보증 기간은?", provider=provider)
    assert (data["status"], data["mode"], data["answer"]) == ("no_evidence", "generative", None)
    assert len(provider.answer_calls) == 2


def test_answer_prompt_asks_the_model_to_say_when_nothing_answers():
    """RAG-03: the model reports a non-answer as a flag, not only in prose."""
    prompt = answer_prompt("보증 기간은?", [], None)
    assert "answered" in prompt and "false" in prompt


def test_provider_outage_falls_back_to_the_original_sentences(ask):
    """ENGINE-02: a timeout does not fail the request."""
    provider = ScriptedProvider(answers=[TransientProviderError("timeout")])
    data = ask("보증 기간은?", provider=provider)
    assert (data["status"], data["mode"]) == ("answered", "extractive") and len(provider.answer_calls) == 1


# ── cost filter ─────────────────────────────────────────────────────────────
def test_cost_visible_team_gets_the_price(ask):
    data = ask("목표가는 얼마인가요?")
    assert data["status"] == "answered" and SECRET in data["answer"]["finding"]["text"]
    assert cited(data)[0] == ("2.2", 3)


def test_restricted_team_is_told_the_topic_is_hidden_and_nothing_else(ask, index: SearchIndex):
    """INV-4, INV-8: a cost question is refused before retrieval; the topics offered are open ones."""
    data = ask("목표가는 얼마인가요?", who="no_cost")
    assert (data["status"], data["answer"]) == ("restricted", None)
    assert data["suggestions"] == ["납기", "인도조건", "보증", "인증"]
    assert index.scoped(ALL_IDS).suggestions() == ["납기", "인도조건", "보증", "목표가"]
    assert ask("납기와 결제 조건은?", who="no_cost")["status"] == "restricted"
    for secret in (SECRET, "1.2345", "target price", "Zielvorgabe", "rejected"):
        assert secret not in json.dumps(data, ensure_ascii=False)


def test_no_question_reaches_hidden_text_for_a_restricted_team(ask, index: SearchIndex):
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
        hits = index.scoped(OPEN_IDS).search(question).hits
        assert not hidden & {hit.sentence.locus.key() for hit in hits}, question
        data = ask(question, who="no_cost")
        assert SECRET not in json.dumps(data, ensure_ascii=False), question
    assert "목표가" not in index.scoped(OPEN_IDS).suggestions()
    assert index.scoped(ALL_IDS).search("target price").hits[0].clause_id == "d01:2.2"


def test_unknown_group_reads_the_reduced_index(index: SearchIndex):
    """INV-8, fail closed: a group value nobody defined is treated as restricted."""
    assert not index.search("target price", group="auditors").hits  # type: ignore[arg-type]


def test_restricted_team_still_gets_answers_about_open_clauses(ask, index: SearchIndex):
    data = ask("인도조건은?", who="no_cost")
    assert data["status"] == "answered" and "FCA" in data["answer"]["finding"]["text"]
    assert not index.is_cost_question("인도조건은?") and index.is_cost_question("결제 조건은?")


# ── department scope ────────────────────────────────────────────────────────
def test_a_scoped_index_finds_only_its_clauses(index: SearchIndex):
    """INV-4, auth-rbac §4: the scoped index holds the sentences of the given clauses and no others."""
    scoped = index.scoped(frozenset({"d01:2.1"}))
    assert {hit.clause_id for hit in scoped.search("납기는 어떻게 되나요?").hits} == {"d01:2.1"}
    for question in ("보증 기간", "certification", "목표가", "IATF 16949", "Cpk"):
        assert not scoped.search(question).hits, question
    assert not index.scoped(frozenset()).search("delivery").hits, "no clause, no answer"
    assert {h.clause_id for h in index.search("보증 기간", group="cost_visible").hits} == {"d01:4.5"}, (
        "the unscoped index is unchanged"
    )


def test_a_scoped_index_scores_as_if_the_other_clauses_did_not_exist(index: SearchIndex, ruleset: Ruleset):
    """INV-4: BM25 statistics, missing words and topics come from the scope's own text only, so a hidden
    clause cannot be felt through ranking, the missing list or the suggestions."""
    ids = frozenset({"d01:2.1", "d01:5.1"})
    scoped, alone = index.scoped(ids), SearchIndex([DELIVERY, QUALITY], ruleset).scoped(ids)
    for question in ("delivery and certification", "warranty for the supplier", "Gewährleistung", "납기"):
        assert scoped.search(question) == alone.search(question), question
    assert scoped.suggestions() == alone.suggestions() == ["납기", "인도조건", "인증", "공정능력"]
    assert scoped.search("certification gewährleistungsfrist").missing == ("gewährleistungsfrist",)


def test_an_answer_is_built_from_the_scope_only(ask):
    """auth-rbac §4: each department is answered from its own clauses; others find no evidence."""
    assert "FCA" in ask("인도조건은?", who="logistics")["answer"]["finding"]["text"]
    assert ask("인도조건은?", who="quality")["status"] == "no_evidence"
    assert "IATF" in ask("인증 조건은?", who="quality")["answer"]["finding"]["text"]
    hidden = ask("인증 조건은?", who="logistics")
    assert (hidden["status"], hidden["answer"]) == ("no_evidence", None)
    assert "인증" not in hidden["suggestions"], "a topic only hidden clauses cover is not offered"


def test_evidence_outside_the_visible_clauses_is_never_used(index: SearchIndex, ruleset: Ruleset):
    """INV-4, fail closed: if the index ever returned a sentence that is not visible, nothing is answered
    and the model is not asked."""
    provider = ScriptedProvider(answers=[Answer(text="36개월입니다.", citations=(1,), answered=True)])
    data = answer_question(
        "보증 기간은?",
        index=index.scoped(ALL_IDS),
        clauses=(DELIVERY,),
        scope=scope_for("logistics", ruleset),
        provider=provider,
    )
    assert (data["status"], data["answer"], data["suggestions"]) == ("no_evidence", None, [])
    assert provider.answer_calls == []
