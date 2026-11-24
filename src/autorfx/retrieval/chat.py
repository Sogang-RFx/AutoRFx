"""Question answering over one RFQ (RAG-03, RAG-04, CITE-01, INV-3).

The answer is built from retrieved sentences only. Without an LLM it is those
sentences, word for word ("extractive"). With an LLM the model writes the
answer and cites sentence numbers; code maps numbers to positions and checks
them, including every number in the text. A failed check is retried once,
then the original sentences are shown instead. When the evidence is too thin
nothing is answered.
"""

from __future__ import annotations

from collections.abc import Sequence

from autorfx.access import hidden_keys
from autorfx.analysis.citation import verify
from autorfx.analysis.providers import (
    AnalysisProvider,
    Answer,
    EvidenceSentence,
    RuleOnlyProvider,
    TransientProviderError,
)
from autorfx.models import Clause, Finding, Group, VerifiedFinding
from autorfx.retrieval.index import SearchIndex

MAX_EVIDENCE = 6
MAX_QUESTION = 500
ATTEMPTS = 2
FEEDBACK = "cite only the sentence numbers given, and state no number that those sentences do not contain."
MESSAGES = {
    "extractive": "원문에서 찾은 문장입니다. LLM이 연결되지 않아 요약하지 않고 그대로 보여 드립니다.",
    "generative": "원문 문장을 근거로 작성한 답변입니다. 인용한 문장을 눌러 확인하세요.",
    "fallback": "작성된 답변이 인용 검증을 통과하지 못해, 찾은 원문 문장을 그대로 보여 드립니다.",
    "no_evidence": "이 RFQ에서 근거가 되는 문장을 찾지 못했습니다. 근거 없이 답하지 않습니다.",
    "restricted": "금액과 관련된 질문은 이 팀에서 답할 수 없습니다. 금액 외의 주제로 질문해 주세요.",
}


def _reply(
    status: str, mode: str, answer: VerifiedFinding | None, message: str, suggestions: Sequence[str]
) -> dict:
    return {
        "status": status,
        "mode": mode,
        "answer": answer.model_dump(mode="json") if answer else None,
        "message": message,
        "suggestions": list(suggestions),
    }


def _check(
    reply: Answer,
    evidence: Sequence[EvidenceSentence],
    clauses: Sequence[Clause],
    *,
    check_numbers: bool,
) -> VerifiedFinding | None:
    """One reply with its citations checked. None when it cites nothing usable."""
    by_number = {e.number: e.locus for e in evidence}
    if not reply.text.strip() or not reply.citations or any(n not in by_number for n in reply.citations):
        return None
    finding = Finding(
        kind="answer",
        text=reply.text.strip(),
        citations=tuple(by_number[n] for n in dict.fromkeys(reply.citations)),
    )
    allowed = {e.locus.key() for e in evidence}
    return verify(finding, clauses, allowed=allowed, check_numbers=check_numbers)


def answer_question(
    question: str,
    *,
    index: SearchIndex,
    clauses: Sequence[Clause],
    group: Group,
    provider: AnalysisProvider,
) -> dict:
    """The ``data`` object of ``contract/chat.schema.json``."""
    question = question.strip()
    if not question:
        raise ValueError("질문을 입력해 주세요.")
    if len(question) > MAX_QUESTION:
        raise ValueError(f"질문은 {MAX_QUESTION}자 이내로 입력해 주세요.")
    mode = "generative" if provider.uses_llm else "extractive"
    # Decided before anything is retrieved (INV-4): the rules that hide a clause also name the question.
    if group != "cost_visible" and index.is_cost_question(question):
        return _reply("restricted", mode, None, MESSAGES["restricted"], index.suggestions(group))
    result = index.search(question, group=group)
    if not result.passed:
        return _reply("no_evidence", mode, None, MESSAGES["no_evidence"], index.suggestions(group))

    # A partial match is said to be one: the words the visible text never uses are named.
    partial = (
        f" 질문의 다음 표현은 열람할 수 있는 문서에 없습니다: {', '.join(result.missing)}."
        if result.missing
        else ""
    )
    visible = [c for c in clauses if group == "cost_visible" or not c.is_cost_sensitive]
    evidence = [
        EvidenceSentence(number=number, text=hit.sentence.text, locus=hit.sentence.locus)
        for number, hit in enumerate(result.hits[:MAX_EVIDENCE], start=1)
    ]
    # Fail closed: evidence comes from the reduced index, so this can only trip on a defect.
    if group != "cost_visible" and hidden_keys(clauses) & {e.locus.key() for e in evidence}:
        return _reply("no_evidence", mode, None, MESSAGES["no_evidence"], [])

    if provider.uses_llm:
        feedback: str | None = None
        for _ in range(ATTEMPTS):
            try:
                reply = provider.answer(question, evidence, feedback)
            except TransientProviderError:
                break
            if not reply.answered:
                # The model judged the evidence not to answer. A "no information" sentence would pass the
                # citation check, and quoting the sentences instead would contradict the judgement (RAG-03).
                return _reply(
                    "no_evidence", "generative", None, MESSAGES["no_evidence"], index.suggestions(group)
                )
            answer = _check(reply, evidence, visible, check_numbers=True)
            if answer is not None and answer.citation_status == "verified":
                return _reply("answered", "generative", answer, MESSAGES["generative"] + partial, [])
            feedback = FEEDBACK
    extract = _check(
        RuleOnlyProvider().answer(question, evidence, None), evidence, visible, check_numbers=False
    )
    if extract is None or extract.citation_status != "verified":
        return _reply("no_evidence", "extractive", None, MESSAGES["no_evidence"], index.suggestions(group))
    message = MESSAGES["fallback" if provider.uses_llm else "extractive"]
    return _reply("answered", "extractive", extract, message + partial, [])
