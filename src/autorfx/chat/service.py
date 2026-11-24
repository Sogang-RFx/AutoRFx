"""Question answering over one RFQ (RAG-03, RAG-04, CITE-01, CITE-02, INV-3, docs/design/chat.md §2, §3.5).

A question is planned, answered from stored results or from gathered evidence, and every statement
is verified on its own before it is shown. Without an LLM the answer is original sentences that pass
the strict evidence gate. At most three LLM calls are made per question.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from autorfx.analysis.citation import verify
from autorfx.analysis.providers import TransientProviderError
from autorfx.auth.policy import Scope
from autorfx.chat.checks import Outcome, checked
from autorfx.chat.gather import MAX_QUERIES, gather
from autorfx.chat.model import MAX_TURNS, ChatModel
from autorfx.chat.models import ChatContext, CitedText, Draft, Evidence, Plan, Turn
from autorfx.chat.stored import citable_keys, conflict_statements, red_flag_statements, related_conflicts
from autorfx.chat.topics import plan_by_rules
from autorfx.models import Finding, VerifiedFinding

log = logging.getLogger(__name__)

MAX_QUESTION = 500
MAX_EXTRACT = 3
MAX_STATEMENTS = {"overview": 12, "lookup": 15}
ENOUGH_FOR_OVERVIEW = 3
"""With this many verified statements an overview is not written again: a second draft doubles the wait
and an overview leaves things out anyway. A looked-up fact is worth the retry."""
MESSAGES = {
    "extractive": "원문에서 찾은 문장입니다. LLM이 연결되지 않아 요약하지 않고 그대로 보여 드립니다.",
    "generative": "원문 문장을 근거로 작성한 답변입니다. 인용한 문장을 눌러 확인하세요.",
    "fallback": "작성된 답변이 인용 검증을 통과하지 못해, 찾은 원문 문장을 그대로 보여 드립니다.",
    "outage": "지금은 답변을 작성할 수 없어, 찾은 원문 문장을 그대로 보여 드립니다.",
    "no_evidence": "이 RFQ에서 근거가 되는 문장을 찾지 못했습니다. 근거 없이 답하지 않습니다.",
    "restricted": "금액과 관련된 질문은 이 팀에서 답할 수 없습니다. 금액 외의 주제로 질문해 주세요.",
    "guide": "이 RFQ에 대해 무엇이 궁금한지 구체적으로 적어 주세요. 답에는 원문 위치가 함께 표시됩니다.",
    "dropped": "인용 검증을 통과하지 못한 문장은 뺐습니다.",
    "incomplete": "질문의 일부는 찾은 문장에서 확인하지 못했습니다.",
    "red_flags": "분석 단계에서 표시된 독소조항입니다.",
    "conflicts": "문서 간 정합성 검사에서 찾은 항목입니다.",
    "no_red_flags": "분석에서 표시된 독소조항이 없습니다.",
    "no_conflicts": "문서 사이에서 찾은 불일치가 없습니다.",
    # Whether or not something is hidden: "there are none" would be false, "some are hidden" a hint (INV-8).
    "no_red_flags_restricted": "열람할 수 있는 조항에서는 표시된 독소조항을 찾지 못했습니다.",
    "no_conflicts_restricted": "열람할 수 있는 조항에서는 문서 간 불일치를 찾지 못했습니다.",
}


@dataclass(frozen=True)
class _Request:
    context: ChatContext
    scope: Scope
    plan: Plan


def answer_question(
    question: str,
    *,
    context: ChatContext,
    scope: Scope,
    model: ChatModel | None,
    history: Sequence[Turn] = (),
) -> dict:
    """The ``data`` object of ``contract/chat.schema.json``."""
    question = question.strip()
    if not question:
        raise ValueError("질문을 입력해 주세요.")
    if len(question) > MAX_QUESTION:
        raise ValueError(f"질문은 {MAX_QUESTION}자 이내로 입력해 주세요.")
    if _is_refused(question, context, scope):
        return _restricted(context)
    plan = _plan(question, history, context, model)
    # "그럼 그건 얼마야?" names no price; the question it stands for does (RAG-04).
    if _is_refused(plan.question, context, scope):
        return _restricted(context)
    request = _Request(context, scope, plan)
    if plan.intent == "guide":
        return _guide(request)
    if plan.intent in ("red_flags", "conflicts"):
        return _stored(request)
    if model is None:
        return _extract(request, "extractive")
    return _generate(request, model)


def _failure(exc: Exception) -> str:
    """The kind of failure only: a client message may quote the prompt, which holds document text."""
    return type(exc.__cause__ or exc).__name__


# ── planning ────────────────────────────────────────────────────────────────
def _is_refused(question: str, context: ChatContext, scope: Scope) -> bool:
    """Decided before anything is retrieved, from the question and the rules alone (INV-4)."""
    return not scope.cost_visible and context.index.is_cost_question(question)


def _plan(question: str, history: Sequence[Turn], context: ChatContext, model: ChatModel | None) -> Plan:
    if model is None:
        return plan_by_rules(question, context.topics)
    languages = sorted({f.lang for f in context.files if f.lang != "unknown"})
    try:
        plan = model.plan(question, tuple(history)[-MAX_TURNS:], languages)
    except TransientProviderError as exc:
        log.warning("chat plan failed (%s); planning by rules", _failure(exc))
        return plan_by_rules(question, context.topics)
    by_rule = plan_by_rules(question, context.topics).intent
    # Rules first: now and then "불리한 조항이 뭐야?" was taken for a question about the tool.
    intent = by_rule if plan.intent == "guide" and by_rule != "lookup" else plan.intent
    return Plan(
        intent=intent,
        question=plan.question.strip()[:MAX_QUESTION] or question,
        queries=tuple(dict.fromkeys(q.strip() for q in plan.queries if q.strip()))[:MAX_QUERIES],
    )


# ── replies ─────────────────────────────────────────────────────────────────
def _reply(
    status: str,
    mode: str,
    message: str,
    *,
    statements: Sequence[CitedText] = (),
    answer: VerifiedFinding | None = None,
    conflicts: Sequence[dict] = (),
    suggestions: Sequence[str] = (),
) -> dict:
    return {
        "status": status,
        "mode": mode,
        "answer": answer.model_dump(mode="json") if answer else None,
        "statements": [s.model_dump(mode="json") for s in statements],
        "conflicts": list(conflicts),
        "message": message,
        "suggestions": list(suggestions),
    }


def _restricted(context: ChatContext) -> dict:
    suggestions = context.index.suggestions()
    return _reply("restricted", "extractive", MESSAGES["restricted"], suggestions=suggestions)


def _no_evidence(request: _Request, mode: str, message: str = MESSAGES["no_evidence"]) -> dict:
    suggestions = request.context.index.suggestions()
    return _reply("no_evidence", mode, message, suggestions=suggestions)


def _guide(request: _Request) -> dict:
    topics = [t.label for t in request.context.topics if t.intent != "guide"]
    suggestions = list(dict.fromkeys([*topics, *request.context.index.suggestions()]))
    return _reply("guide", "stored", MESSAGES["guide"], suggestions=suggestions)


def _answered(
    request: _Request,
    mode: str,
    statements: Sequence[CitedText],
    message: str,
    *,
    with_conflicts: bool = True,
) -> dict:
    """An answer whose every position is one this scope may be shown (INV-3, INV-8)."""
    context, scope = request.context, request.scope
    loci = tuple({c.key(): c for s in statements for c in s.citations}.values())
    finding = Finding(kind="answer", text="\n".join(s.text for s in statements), citations=loci)
    answer = verify(finding, context.clauses, allowed=citable_keys(context, scope))
    if answer.citation_status != "verified":
        return _reply("no_evidence", mode, MESSAGES["no_evidence"])
    keys = {locus.key() for locus in loci}
    conflicts = related_conflicts(keys, context, scope) if with_conflicts else ()
    return _reply("answered", mode, message, statements=statements, answer=answer, conflicts=conflicts)


def _stored(request: _Request) -> dict:
    intent = request.plan.intent
    read = red_flag_statements if intent == "red_flags" else conflict_statements
    statements = read(request.context, request.scope)
    if not statements:
        suffix = "" if request.scope.is_full else "_restricted"
        return _no_evidence(request, "stored", MESSAGES[f"no_{intent}{suffix}"])
    return _answered(request, "stored", statements, MESSAGES[intent], with_conflicts=False)


def _extract(request: _Request, message: str) -> dict:
    """Original sentences, word for word, behind the strict gate (RAG-03)."""
    result = request.context.index.search(request.plan.question)
    if not result.passed:
        return _no_evidence(request, "extractive")
    found = [h.sentence for h in result.hits[:MAX_EXTRACT]]
    statements = [CitedText(text=s.text, citations=(s.locus,)) for s in found]
    # A partial match is said to be one: the words the visible text never uses are named.
    partial = (
        f" 질문의 다음 표현은 열람할 수 있는 문서에 없습니다: {', '.join(result.missing)}."
        if result.missing
        else ""
    )
    return _answered(request, "extractive", statements, MESSAGES[message] + partial)


# ── generated answers ───────────────────────────────────────────────────────
def _generate(request: _Request, model: ChatModel) -> dict:
    plan, context, scope = request.plan, request.context, request.scope
    evidence = gather(
        plan.question, plan.queries, context=context, scope=scope, overview=plan.intent == "overview"
    )
    # Fail closed: gathering reads visible clauses only, so this can only trip on a defect (INV-8).
    if not {e.locus.key() for e in evidence} <= citable_keys(context, scope):
        log.error("chat evidence outside the visible clauses; not answered")
        return _reply("no_evidence", "extractive", MESSAGES["no_evidence"])
    if not evidence:
        return _no_evidence(request, "extractive")
    outcome = _write(request, model, evidence)
    if not outcome.answered:
        # Neither shown nor replaced by quotes: that would contradict the model's judgement (RAG-03).
        return _no_evidence(request, "generative")
    if not outcome.statements:
        return _extract(request, "outage" if outcome.failed else "fallback")
    notes = [MESSAGES["generative"]]
    if outcome.dropped:
        notes.append(MESSAGES["dropped"])
    if not outcome.complete:
        notes.append(MESSAGES["incomplete"])
    shown = outcome.statements[: MAX_STATEMENTS.get(plan.intent, MAX_STATEMENTS["lookup"])]
    return _answered(request, "generative", shown, " ".join(notes))


def _write(request: _Request, model: ChatModel, evidence: Sequence[Evidence]) -> Outcome:
    """A draft, and one retry that names what was wrong with it (CITE-02)."""
    first = _draft(request, model, evidence, None)
    if first is None:
        return Outcome(failed=True)
    outcome, problems = checked(first, evidence)
    if not problems or not outcome.answered:
        return outcome
    if request.plan.intent == "overview" and len(outcome.statements) >= ENOUGH_FOR_OVERVIEW:
        return outcome
    second = _draft(request, model, evidence, "\n".join(problems))
    if second is None:
        return outcome
    retried = checked(second, evidence)[0]
    # A retry that went worse does not lose what was verified; a judgement that nothing answers stands.
    worse = retried.answered and len(retried.statements) < len(outcome.statements)
    return outcome if worse else retried


def _draft(
    request: _Request, model: ChatModel, evidence: Sequence[Evidence], feedback: str | None
) -> Draft | None:
    overview = request.plan.intent == "overview"
    try:
        return model.answer(request.plan.question, evidence, overview=overview, feedback=feedback)
    except TransientProviderError as exc:
        log.warning("chat answer failed (%s)", _failure(exc))
        return None
