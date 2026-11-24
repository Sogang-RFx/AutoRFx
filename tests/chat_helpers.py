"""Shared by the chat tests: a small package, its context, and one checked way to ask a question.

Only chat tests import this module, so a missing ``autorfx.chat`` breaks nothing else.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from autorfx.chat.model import ScriptedChatModel
from autorfx.chat.models import ChatContext, Draft, Statement, Topic, Turn
from autorfx.chat.service import answer_question
from autorfx.models import Clause, ClauseAnalysis, ConsistencyIssue, FileRecord
from autorfx.retrieval import SearchIndex
from autorfx.retrieval.dense import DenseIndex
from autorfx.rules import Ruleset
from contract_schema import assert_valid
from factories import make_clause, make_file

SECRET = "1.2345 EUR"
SECRETS = (SECRET, "1.2345", "target price", "Zielvorgabe", "will be rejected")
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

# The package is small, so every visible sentence is evidence, numbered in document order (§3.3).
WARRANTY_S, REPLACEMENT_S, IATF_S = 5, 6, 7
"""Evidence numbers for a cost-visible team. A restricted team has no PRICE: two less."""
PRICE_S = 3
HIDDEN = 2

TOPICS = (
    Topic(intent="conflicts", label="문서 간 불일치", patterns=("문서끼리",)),
    Topic(intent="red_flags", label="불리한 조항", patterns=("독소",)),
    Topic(intent="overview", label="RFQ 요약", patterns=("RFQ 요약",)),
    Topic(intent="guide", label="사용 안내", patterns=("안녕",)),
)


def make_context(
    ruleset: Ruleset,
    clauses: Sequence[Clause] = CLAUSES,
    *,
    analyses: Sequence[ClauseAnalysis] = (),
    issues: Sequence[ConsistencyIssue] = (),
    files: Sequence[FileRecord] | None = None,
    topics: Sequence[Topic] = TOPICS,
    dense: DenseIndex | None = None,
) -> ChatContext:
    names = dict.fromkeys(c.source_doc for c in clauses)
    return ChatContext(
        index=SearchIndex(clauses, ruleset, dense=dense),
        clauses=tuple(clauses),
        analyses=tuple(analyses),
        issues=tuple(issues),
        files=tuple(files) if files is not None else tuple(make_file(n, f"{n}.pdf") for n in names),
        topics=tuple(topics),
    )


def draft(*statements: tuple[str, tuple[int, ...]], answered: bool = True, complete: bool = True) -> Draft:
    return Draft(
        answered=answered,
        complete=complete,
        statements=tuple(Statement(text=text, citations=numbers) for text, numbers in statements),
    )


def ask(
    context: ChatContext,
    question: str,
    *,
    group: str = "cost_visible",
    model: ScriptedChatModel | None = None,
    history: Sequence[Turn] = (),
) -> dict:
    """``answer_question`` plus what must hold for every reply, whatever path it took.

    INV-9: the shape is the contract. INV-3: what is shown carries citations, and ``answer`` is exactly
    the statements joined. §2: at most one plan call and two answer calls.
    """
    data = answer_question(question, context=context, group=group, model=model, history=history)  # type: ignore[arg-type]
    assert_valid("chat", {"data": data, "provenance": {"chat": "real"}})
    assert (data["status"] == "answered") == (data["answer"] is not None)
    if data["answer"] is None:
        assert data["statements"] == []
    else:
        finding = data["answer"]["finding"]
        assert data["answer"]["citation_status"] == "verified" and finding["kind"] == "answer"
        assert finding["text"] == "\n".join(s["text"] for s in data["statements"])
        merged: list[dict] = []
        for locus in (c for s in data["statements"] for c in s["citations"]):
            if locus not in merged:
                merged.append(locus)
        assert finding["citations"] == merged and merged
    if model is not None:
        assert len(model.plan_calls) <= 1 and len(model.answer_calls) <= 2
    return data


def cited(data: dict) -> list[tuple[str, int]]:
    return [(c["section_id"], c["sentence_index"]) for c in data["answer"]["finding"]["citations"]]


def dumped(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)
