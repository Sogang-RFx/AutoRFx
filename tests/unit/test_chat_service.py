"""Question answering over one RFQ: intent, evidence, verification, and the cost filter (§2, §3.5).

Every reply goes through ``chat_helpers.ask``, which checks the contract, the citations and the call limit.
"""

from __future__ import annotations

import pytest

from autorfx.access import hidden_keys
from autorfx.analysis import TransientProviderError
from autorfx.chat.model import ScriptedChatModel
from autorfx.chat.models import ChatContext, Evidence, Plan, Turn
from autorfx.retrieval.dense import DenseIndex
from autorfx.rules import Ruleset
from chat_helpers import (
    CLAUSES,
    HIDDEN,
    IATF_S,
    PRICE,
    PRICE_S,
    QUALITY,
    REPLACEMENT_S,
    SECRET,
    SECRETS,
    WARRANTY,
    WARRANTY_S,
    ask,
    cited,
    draft,
    dumped,
    make_context,
)
from factories import make_analysis, make_clause, make_issue
from topic_encoder import CLAUSES as TOPIC_CLAUSES
from topic_encoder import TopicEncoder

DOWN = TransientProviderError("timeout")
GOOD = ("보증 기간은 36개월입니다.", (WARRANTY_S,))
FREE = ("불량 부품은 무상으로 교체합니다.", (REPLACEMENT_S,))
BAD = ("보증 기간은 24개월입니다.", (WARRANTY_S,))
DROPPED = "인용 검증을 통과하지 못한 문장은 뺐습니다."
INCOMPLETE = "질문의 일부는 찾은 문장에서 확인하지 못했습니다."
QUESTION = "보증 기간은?"
RESTRICTED_GROUPS = ["cost_restricted", "auditors"]


@pytest.fixture(scope="module")
def context(ruleset: Ruleset) -> ChatContext:
    return make_context(ruleset)


def plan(intent: str, question: str = QUESTION, *queries: str) -> Plan:
    return Plan(intent=intent, question=question, queries=queries)  # type: ignore[arg-type]


def texts(data: dict) -> list[str]:
    return [s["text"] for s in data["statements"]]


# ── without a model ─────────────────────────────────────────────────────────
def test_without_an_llm_the_answer_is_the_original_sentences(context: ChatContext):
    """RAG-03, INV-12: extractive mode quotes; it says so and cites every sentence it shows."""
    data = ask(context, "What is the warranty period?")
    assert (data["status"], data["mode"]) == ("answered", "extractive")
    originals = {s.text for c in CLAUSES for s in c.sentences}
    assert texts(data)[0] == WARRANTY.sentences[0].text and set(texts(data)) <= originals
    assert cited(data)[0] == ("4.5", 5) and "LLM이 연결되지 않아" in data["message"]
    assert data["message"].endswith("문서에 없습니다: period."), "a partial match is said to be one"
    assert "문서에 없습니다" not in ask(context, "보증")["message"]


def test_no_evidence_gives_no_answer_and_offers_topics(context: ChatContext):
    """RAG-03, INV-3: nothing is answered without evidence; the topics offered exist in the text."""
    data = ask(context, "Who is the CEO?")
    assert (data["status"], data["mode"]) == ("no_evidence", "extractive")
    assert data["suggestions"][:2] == ["납기", "인도조건"] and "보증" in data["suggestions"]


def test_empty_or_oversized_question_is_refused(context: ChatContext):
    with pytest.raises(ValueError, match="질문을 입력"):
        ask(context, "   ")
    with pytest.raises(ValueError, match="500자"):
        ask(context, "x" * 501)


def test_a_paraphrase_is_found_by_the_dense_index_without_an_llm(ruleset: Ruleset):
    """RAG-01, RAG-03: the strict gate stays in place when no model judges the evidence."""
    dense = DenseIndex(TOPIC_CLAUSES, TopicEncoder(), min_score=0.5)
    context = make_context(ruleset, TOPIC_CLAUSES, dense=dense)
    data = ask(context, "부품을 어떤 상자에 담아서 보내야 하나요?", group="cost_restricted")
    assert (data["status"], data["mode"]) == ("answered", "extractive")
    assert data["answer"]["finding"]["citations"][0]["section_id"] == "3.2"
    assert ask(context, "대표이사 이름은?", group="cost_restricted")["status"] == "no_evidence"


@pytest.mark.parametrize("model", [None, "scripted"])
def test_a_greeting_gets_the_guide(context: ChatContext, model: str | None):
    """INV-12: a fixed text is not labelled as generated, and no answer is written."""
    scripted = ScriptedChatModel(plans=[plan("guide", "안녕하세요")], drafts=[]) if model else None
    data = ask(context, "안녕하세요", model=scripted)
    assert data["status"] == "guide" and data["mode"] != "generative" and data["message"]
    assert scripted is None or scripted.answer_calls == []


# ── generated answers ───────────────────────────────────────────────────────
def test_a_verified_answer_is_shown_with_its_citations(context: ChatContext):
    """CITE-01: the model cites sentence numbers; code turns them into positions and verifies them."""
    model = ScriptedChatModel(drafts=[draft(GOOD, FREE)])
    data = ask(context, QUESTION, model=model)
    assert (data["status"], data["mode"]) == ("answered", "generative")
    assert texts(data) == [GOOD[0], FREE[0]] and cited(data) == [("4.5", 5), ("4.5", 6)]
    assert DROPPED not in data["message"] and INCOMPLETE not in data["message"]
    ((question, evidence, overview, feedback),) = model.answer_calls
    assert (question, overview, feedback) == (QUESTION, False, None)
    assert [e.text for e in evidence] == [s.text for c in CLAUSES for s in c.sentences]


def test_a_german_date_may_be_written_in_korean(ruleset: Ruleset):
    """CITE-01, §1: "04.12.2026" as "2026년 12월 4일" used to throw the whole answer away."""
    clause = make_clause("1.3", ["Die Abgabefrist endet am 04.12.2026."], lang="de")
    model = ScriptedChatModel(drafts=[draft(("2026년 12월 4일까지 제출합니다.", (1,)))])
    data = ask(make_context(ruleset, [clause]), "마감은?", model=model)
    assert data["mode"] == "generative" and len(model.answer_calls) == 1


def test_each_statement_is_checked_against_its_own_citations(context: ChatContext):
    """CITE-01, INV-3: 36 is in the evidence, but not in the sentence this statement cites."""
    stray = ("교체 기간은 36개월입니다.", (REPLACEMENT_S,))
    model = ScriptedChatModel(drafts=[draft(GOOD, stray)] * 2)
    assert texts(ask(context, QUESTION, model=model)) == [GOOD[0]]


def test_a_rejected_statement_is_retried_with_the_offending_number(context: ChatContext):
    """CITE-02: the retry is told which number is wrong, not only that something is."""
    model = ScriptedChatModel(drafts=[draft(BAD), draft(GOOD)])
    data = ask(context, QUESTION, model=model)
    assert (data["mode"], texts(data)) == ("generative", [GOOD[0]]) and DROPPED not in data["message"]
    first, second = (call[3] for call in model.answer_calls)
    assert first is None and "24" in second


def test_the_retry_names_a_citation_that_does_not_exist(context: ChatContext):
    model = ScriptedChatModel(drafts=[draft(("36개월", (99,))), draft(GOOD)])
    assert ask(context, QUESTION, model=model)["mode"] == "generative"
    assert "99" in model.answer_calls[1][3]


def test_after_the_second_failure_only_the_bad_statement_is_dropped(context: ChatContext):
    """CITE-02, INV-3: what was verified is shown, and the reader is told something is missing."""
    model = ScriptedChatModel(drafts=[draft(GOOD, BAD)] * 2)
    data = ask(context, QUESTION, model=model)
    assert (data["mode"], texts(data)) == ("generative", [GOOD[0]]) and DROPPED in data["message"]
    assert len(model.answer_calls) == 2


def test_a_failed_retry_keeps_what_passed_the_first_time(context: ChatContext):
    """ENGINE-02: an outage on the retry does not lose the verified statements."""
    data = ask(context, QUESTION, model=ScriptedChatModel(drafts=[draft(GOOD, BAD), DOWN]))
    assert (data["mode"], texts(data)) == ("generative", [GOOD[0]]) and DROPPED in data["message"]


@pytest.mark.parametrize(
    "bad",
    [
        BAD,
        ("36개월", (99,)),
        ("36개월", (WARRANTY_S, 99)),
        ("보증이 있습니다.", ()),
        ("  ", (WARRANTY_S,)),
        ("36개월", (0,)),
    ],
    ids=["number", "unknown", "one-unknown", "uncited", "blank", "zero"],
)
def test_with_nothing_left_the_original_sentences_are_shown(context: ChatContext, bad: tuple):
    """CITE-01, INV-3, INV-12: an answer nothing supports is replaced, and not labelled generative."""
    data = ask(context, QUESTION, model=ScriptedChatModel(drafts=[draft(bad)] * 2))
    assert (data["status"], data["mode"]) == ("answered", "extractive")
    assert texts(data)[0] == WARRANTY.sentences[0].text and "24" not in dumped(data["statements"])
    assert "검증을 통과하지 못해" in data["message"]


def test_an_answer_without_statements_is_not_an_answer(context: ChatContext):
    """INV-3: answered=true with nothing in it must not become an empty answer."""
    data = ask(context, QUESTION, model=ScriptedChatModel(drafts=[draft()] * 2))
    assert (data["status"], data["mode"]) == ("answered", "extractive")


def test_with_nothing_left_and_no_strict_evidence_nothing_is_answered(context: ChatContext):
    """RAG-03: the extractive fallback keeps the strict gate."""
    question = "Who is the CEO?"
    model = ScriptedChatModel(drafts=[draft(("대표는 24세입니다.", (WARRANTY_S,)))] * 2)
    data = ask(context, question, model=model)
    assert data["status"] == "no_evidence" and data["mode"] != "generative"


def test_when_the_model_finds_no_answer_nothing_is_shown(context: ChatContext):
    """RAG-03, INV-3: a judged non-answer is neither shown, retried, nor replaced by quoted sentences."""
    model = ScriptedChatModel(drafts=[draft(("그 정보가 없습니다.", (1,)), answered=False)])
    data = ask(context, QUESTION, model=model)
    assert (data["status"], data["mode"]) == ("no_evidence", "generative")
    assert data["suggestions"], "the topics the text does cover are offered"
    assert len(model.answer_calls) == 1


def test_a_non_answer_on_the_retry_is_still_no_evidence(context: ChatContext):
    """RAG-03: not quoted sentences either."""
    data = ask(context, QUESTION, model=ScriptedChatModel(drafts=[draft(BAD), draft(answered=False)]))
    assert (data["status"], data["mode"]) == ("no_evidence", "generative")


def test_a_partial_answer_says_so(context: ChatContext):
    """RAG-03: what could not be found is not passed over."""
    data = ask(context, QUESTION, model=ScriptedChatModel(drafts=[draft(GOOD, complete=False)]))
    assert data["mode"] == "generative" and INCOMPLETE in data["message"]


def test_an_outage_falls_back_to_the_original_sentences(context: ChatContext):
    """ENGINE-02, INV-12: a timeout does not fail the request."""
    for drafts in ([DOWN], [draft(BAD), DOWN]):
        data = ask(context, QUESTION, model=ScriptedChatModel(drafts=drafts))
        assert (data["status"], data["mode"]) == ("answered", "extractive")


def test_conflicting_values_are_attached_to_the_answer(ruleset: Ruleset):
    """INV-3, §3.5: the answer cites one side; the contradiction is shown with both positions."""
    issue = make_issue("c-warranty", [WARRANTY.sentences[0], QUALITY.sentences[1]], title="보증이 다릅니다")
    context = make_context(ruleset, issues=[issue])
    data = ask(context, QUESTION, model=ScriptedChatModel(drafts=[draft(GOOD)]))
    assert [(c["id"], len(c["citations"])) for c in data["conflicts"]] == [("c-warranty", 2)]
    other = ask(context, "인증은?", model=ScriptedChatModel(drafts=[draft(("IATF 16949입니다.", (IATF_S,)))]))
    assert other["mode"] == "generative" and other["conflicts"] == []


# ── planning ────────────────────────────────────────────────────────────────
def test_the_plan_decides_what_is_looked_up(context: ChatContext):
    """RAG-03: the model answers the standalone question; an overview is marked as one."""
    model = ScriptedChatModel(plans=[plan("overview", "이 RFQ의 요약은?", "warranty")], drafts=[draft(GOOD)])
    assert ask(context, "요약은?", model=model)["mode"] == "generative"
    assert model.answer_calls[0][0] == "이 RFQ의 요약은?" and model.answer_calls[0][2] is True


@pytest.mark.parametrize(("question", "queries"), [("보증 기간은?", ()), ("그 조건은?", ("warranty",))])
def test_evidence_is_gathered_for_the_plan(ruleset: Ruleset, question: str, queries: tuple[str, ...]):
    """RAG-03: in a package too large to give whole, the plan's question and queries are what is searched."""
    plain = "General background in plain words. " * 20
    clauses = [make_clause(f"{n}.1", [plain] * 10, page_number=n) for n in range(1, 6)]
    warranty = make_clause("9.1", ["The warranty period is 36 months."], page_number=9)
    model = ScriptedChatModel(
        plans=[plan("lookup", question, *queries)], drafts=[draft(("36개월입니다.", (1,)))]
    )
    data = ask(make_context(ruleset, [*clauses, warranty]), "그럼 그건?", model=model)
    assert [e.text for e in model.answer_calls[0][1]] == [warranty.sentences[0].text]
    assert cited(data) == [("9.1", 1)]


def test_the_planner_is_told_the_document_languages(ruleset: Ruleset):
    """RAG-03: queries have to be written in the languages of the files."""
    german = make_clause("1", ["Die Frist beträgt 36 Monate."], lang="de")
    model = ScriptedChatModel(drafts=[draft(("36개월입니다.", (1,)))])
    files = [f.model_copy(update={"lang": "de"}) for f in make_context(ruleset, [german]).files]
    ask(make_context(ruleset, [german], files=files), QUESTION, model=model)
    assert "de" in model.plan_calls[0][2]


def test_a_failed_plan_falls_back_to_the_rules(ruleset: Ruleset):
    """ENGINE-02: the topic wordings decide the intent; a lookup goes on with the question as asked."""
    context = make_context(ruleset, analyses=[make_analysis(WARRANTY, label="무제한 보증", reason="r")])
    stored = ScriptedChatModel(plans=[DOWN], drafts=[])
    data = ask(context, "독소조항을 알려줘", model=stored)
    assert (data["mode"], texts(data), stored.answer_calls) == ("stored", ["무제한 보증: r"], [])
    lookup = ScriptedChatModel(plans=[DOWN], drafts=[draft(GOOD)])
    assert ask(context, QUESTION, model=lookup)["mode"] == "generative"
    assert lookup.answer_calls[0][0] == QUESTION


def test_a_total_outage_still_answers(context: ChatContext):
    """ENGINE-02: no plan, no answer, and the original sentences are still found."""
    data = ask(context, QUESTION, model=ScriptedChatModel(plans=[DOWN], drafts=[DOWN]))
    assert (data["status"], data["mode"]) == ("answered", "extractive")


@pytest.mark.parametrize("intent", ["red_flags", "conflicts"])
def test_stored_intents_do_not_ask_the_model_for_an_answer(ruleset: Ruleset, intent: str):
    """INV-12: stored results are stored results, with or without a model."""
    context = make_context(
        ruleset,
        analyses=[make_analysis(WARRANTY, label="무제한 보증", reason="r")],
        issues=[make_issue("c-1", list(WARRANTY.sentences))],
    )
    model = ScriptedChatModel(plans=[plan(intent, "문제가 되는 곳은?")], drafts=[])
    data = ask(context, "문제가 되는 곳은?", model=model)
    assert (data["status"], data["mode"], model.answer_calls) == ("answered", "stored", [])


def test_history_is_for_planning_only(context: ChatContext):
    """INV-3: what was said before is not evidence, so a number from it supports nothing."""
    history = (Turn(question="이전 질문입니다", answer="보증은 99개월이라고 했습니다"),)
    fake = draft(("보증은 99개월입니다.", (WARRANTY_S,)))
    model = ScriptedChatModel(plans=[plan("lookup")], drafts=[fake, fake])
    data = ask(context, "그럼 그건?", model=model, history=history)
    assert tuple(model.plan_calls[0][1]) == history
    assert data["mode"] == "extractive" and "99" not in dumped(data)
    for question, evidence, _, _ in model.answer_calls:
        shown = question + dumped([e.model_dump() for e in evidence])
        assert "이전 질문" not in shown and "99개월이라고" not in shown


# ── cost filter ─────────────────────────────────────────────────────────────
def test_cost_visible_team_gets_the_price(context: ChatContext):
    data = ask(context, "목표가는 얼마인가요?")
    assert data["status"] == "answered" and SECRET in data["answer"]["finding"]["text"]
    assert cited(data)[0] == ("2.2", 3)
    model = ScriptedChatModel(drafts=[draft((f"목표가는 {SECRET}입니다.", (PRICE_S,)))])
    assert ask(context, "목표가는 얼마인가요?", model=model)["mode"] == "generative"


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
@pytest.mark.parametrize("question", ["목표가는 얼마인가요?", "납기와 결제 조건은?", "target price"])
def test_a_cost_question_is_refused_before_anything_is_called(
    context: ChatContext, group: str, question: str
):
    """RAG-04, INV-4, INV-8: refused from the question alone; nothing is planned, searched or said."""
    model = ScriptedChatModel(plans=[], drafts=[])
    for used in (model, None):
        data = ask(context, question, group=group, model=used)
        assert data["status"] == "restricted" and data["conflicts"] == [] and data["mode"] != "generative"
        assert data["suggestions"] == ["납기", "인도조건", "보증", "인증"]
        assert not [secret for secret in SECRETS if secret in dumped(data)]
    assert model.plan_calls == [] and model.answer_calls == []


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
@pytest.mark.parametrize("intent", ["lookup", "overview", "conflicts", "red_flags", "guide"])
def test_the_standalone_question_is_checked_for_cost_again(ruleset: Ruleset, group: str, intent: str):
    """RAG-04, INV-4: "그럼 그건 얼마야?" names no price; the question it stands for does."""
    context = make_context(ruleset, analyses=[make_analysis(WARRANTY, label="무제한 보증", reason="r")])
    history = (Turn(question="목표가는?", answer="답할 수 없습니다."),)
    model = ScriptedChatModel(plans=[plan(intent, "목표가는 얼마인가?", "target price")], drafts=[])
    data = ask(context, "그럼 그건 얼마야?", group=group, model=model, history=history)
    assert data["status"] == "restricted" and model.answer_calls == []
    assert not [secret for secret in SECRETS if secret in dumped(data)]


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_hidden_text_reaches_neither_the_model_nor_the_reply(ruleset: Ruleset, group: str):
    """INV-4, INV-8: not as evidence, not in feedback, not through queries the planner wrote."""
    issue = make_issue("c-target_price", [WARRANTY.sentences[0], PRICE.sentences[0]], title=f"값 {SECRET}")
    context = make_context(ruleset, issues=[issue])
    shifted = draft((GOOD[0], (WARRANTY_S - HIDDEN,)), BAD)
    model = ScriptedChatModel(
        plans=[plan("lookup", QUESTION, "target price", "rejected")], drafts=[shifted] * 2
    )
    data = ask(context, QUESTION, group=group, model=model)
    assert texts(data) == [GOOD[0]] and data["conflicts"] == []
    sent = dumped(
        [model.plan_calls, [(q, [e.model_dump() for e in ev], f) for q, ev, _, f in model.answer_calls]]
    )
    assert not [secret for secret in SECRETS if secret in sent + dumped(data)]
    for _, evidence, _, _ in model.answer_calls:
        assert evidence and not hidden_keys(CLAUSES) & {e.locus.key() for e in evidence}


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_hidden_evidence_stops_the_answer(context: ChatContext, group: str, monkeypatch: pytest.MonkeyPatch):
    """INV-8, fail closed: should gathering ever return hidden text, nothing is sent or answered."""
    leaked = PRICE.sentences[0]
    evidence = (Evidence(number=1, text=leaked.text, locus=leaked.locus, file="d01.pdf", title=""),)
    monkeypatch.setattr("autorfx.chat.service.gather", lambda *args, **kwargs: evidence)
    model = ScriptedChatModel(drafts=[draft(("답입니다.", (1,)))])
    data = ask(context, QUESTION, group=group, model=model)
    assert data["status"] == "no_evidence" and model.answer_calls == []
    assert not [secret for secret in SECRETS if secret in dumped(data)]


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
@pytest.mark.parametrize(
    "question",
    ["price", "target price", "rejected quotes", "Zielvorgabe", "1.2345", "목표가", "단가", "delivery price"],
)
def test_no_question_gets_hidden_text_out(context: ChatContext, group: str, question: str):
    """INV-4, RAG-04: whatever is asked, the reply stays outside the cost-sensitive clause."""
    data = ask(context, question, group=group)
    assert SECRET not in dumped(data) and "1.2345" not in dumped(data)


@pytest.mark.parametrize("group", RESTRICTED_GROUPS)
def test_a_restricted_team_still_gets_answers_about_open_clauses(context: ChatContext, group: str):
    data = ask(context, "인도조건은?", group=group)
    assert data["status"] == "answered" and "FCA" in data["answer"]["finding"]["text"]
    model = ScriptedChatModel(drafts=[draft((GOOD[0], (WARRANTY_S - HIDDEN,)))])
    assert ask(context, QUESTION, group=group, model=model)["mode"] == "generative"


# ── tables, caps and the better attempt ─────────────────────────────────────
TABLE = make_clause(
    "Volumes",
    ["Note | x", "Year | 2028 | 2029", "Left [k pcs] | 60 | 120", "Planning figures only."],
    title="Volumes",
    kind="table",
)


def test_the_header_of_a_cited_row_becomes_a_citation(ruleset: Ruleset):
    """CITE-01, INV-3: the years stand in the header, so the header is shown as a source too."""
    reply = draft(("2028년 60 k pcs, 2029년 120 k pcs입니다.", (3,)))
    model = ScriptedChatModel(drafts=[reply])
    data = ask(make_context(ruleset, [TABLE]), "물량은?", model=model)
    assert data["mode"] == "generative" and [n for _, n in cited(data)] == [2, 3]


@pytest.mark.parametrize(
    "text",
    [
        "2030년 60 k pcs입니다.",
        "2028년 70 k pcs입니다.",
        "2029년 60 k pcs입니다.",
        "2028년 120 k pcs, 2029년 60 k pcs입니다.",
        "2029년입니다.",
    ],
)
def test_a_header_does_not_support_what_it_does_not_state(ruleset: Ruleset, text: str):
    """INV-3: the header is added only when it makes every figure of the statement true."""
    model = ScriptedChatModel(drafts=[draft((text, (3,)))] * 2)
    assert ask(make_context(ruleset, [TABLE]), "물량은?", model=model)["mode"] != "generative"


def test_a_sentence_that_is_not_a_table_row_has_no_header(ruleset: Ruleset):
    model = ScriptedChatModel(drafts=[draft(("2028년 계획 수치입니다.", (4,)))] * 2)
    assert ask(make_context(ruleset, [TABLE]), "물량은?", model=model)["mode"] != "generative"


@pytest.mark.parametrize(("intent", "cap"), [("overview", 12), ("lookup", 15)])
def test_an_answer_is_not_the_whole_document(context: ChatContext, intent: str, cap: int):
    """One real overview had 50 statements and took 45 seconds."""
    many = [(f"{GOOD[0]}{' ' * n}", GOOD[1]) for n in range(1)] + [FREE] * 30
    model = ScriptedChatModel(plans=[plan(intent)], drafts=[draft(*many)])
    assert len(texts(ask(context, QUESTION, model=model))) == cap


def test_the_attempt_with_more_verified_statements_is_shown(context: ChatContext):
    """CITE-02: a retry that went worse does not lose what had been verified."""
    model = ScriptedChatModel(drafts=[draft(GOOD, FREE, BAD), draft(GOOD, BAD)])
    data = ask(context, QUESTION, model=model)
    assert texts(data) == [GOOD[0], FREE[0]] and DROPPED in data["message"]


def test_a_retry_without_any_verified_statement_keeps_the_first(context: ChatContext):
    model = ScriptedChatModel(drafts=[draft(GOOD, BAD), draft(BAD)])
    assert texts(ask(context, QUESTION, model=model)) == [GOOD[0]]


def test_an_overview_that_says_enough_is_not_written_twice(context: ChatContext):
    """One real overview took 86 seconds, most of it for a second draft of what was already verified."""
    once = [draft(GOOD, FREE, GOOD, BAD)]
    model = ScriptedChatModel(plans=[plan("overview")], drafts=once)
    data = ask(context, QUESTION, model=model)
    assert len(model.answer_calls) == 1 and len(texts(data)) == 3 and DROPPED in data["message"]


def test_an_overview_that_says_little_is_written_again(context: ChatContext):
    model = ScriptedChatModel(plans=[plan("overview")], drafts=[draft(GOOD, BAD), draft(GOOD, FREE)])
    assert len(texts(ask(context, QUESTION, model=model))) == 2 and len(model.answer_calls) == 2


@pytest.mark.parametrize("cites", [(3,), (2, 3)])
def test_a_value_belongs_to_the_column_of_its_year(ruleset: Ruleset, cites: tuple[int, ...]):
    """INV-3: both figures stand in the table, but 60 is the value of 2028."""
    model = ScriptedChatModel(drafts=[draft(("2029년 60 k pcs입니다.", cites))] * 2)
    assert ask(make_context(ruleset, [TABLE]), "물량은?", model=model)["mode"] != "generative"


def test_an_identifier_brings_its_own_citation(ruleset: Ruleset):
    """CITE-01, INV-3: a part number from another sentence is shown with that sentence, not without one."""
    clause = make_clause("7.1", ["Part B7777-1 as drawn.", "Die Frist beträgt 36 Monate."])
    model = ScriptedChatModel(drafts=[draft(("B7777-1의 보증 기간은 36개월입니다.", (2,)))])
    assert [n for _, n in cited(ask(make_context(ruleset, [clause]), QUESTION, model=model))] == [2, 1]


def test_an_identifier_that_is_nowhere_is_refused(ruleset: Ruleset):
    clause = make_clause("7.1", ["Part B7777-1 as drawn.", "Die Frist beträgt 36 Monate."])
    model = ScriptedChatModel(drafts=[draft(("B7777-2의 보증 기간은 36개월입니다.", (2,)))] * 2)
    assert ask(make_context(ruleset, [clause]), QUESTION, model=model)["mode"] != "generative"


def test_a_clear_wording_is_not_turned_away_as_a_greeting(ruleset: Ruleset):
    """Rules first: the planner once took "독소조항을 알려줘" for a question about the tool."""
    context = make_context(ruleset, analyses=[make_analysis(CLAUSES[2], label="A", reason="b")])
    model = ScriptedChatModel(plans=[plan("guide", "독소조항을 알려줘")])
    assert ask(context, "독소조항을 알려줘", model=model)["mode"] == "stored"
    assert ask(context, "독소조항을 알려줘", model=ScriptedChatModel(plans=[plan("guide")]))["statements"]


def test_the_planner_still_decides_what_the_rules_do_not_know(context: ChatContext):
    model = ScriptedChatModel(plans=[plan("guide", "이 도구는 뭐야?")])
    assert ask(context, "이 도구는 뭐야?", model=model)["status"] == "guide"
