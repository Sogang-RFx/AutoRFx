"""Clause analysis graph (SPEC §18): candidates → confirm → verify → (repair | fallback) → finalize.

One small LangGraph state machine per clause. The path is fixed; the LLM never
chooses the route. Retries are bounded by a counter, not by a recursion limit.
Clauses are independent, so they are run through the compiled graph in a thread
pool and the results are returned in input order (INV-7).

Determinism is enforced outside the model: the candidates are a pure function
of text and rules, provider output is validated against the rule files, the
model cites only sentence numbers, and :func:`autorfx.analysis.citation.verify`
decides whether the result is a normal one. A failed LLM answer is retried once
with the reason, then replaced by the rule result (``origin = rule_fallback``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import unicodedata
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from autorfx.analysis.candidates import Candidates, find_candidates
from autorfx.analysis.citation import verify
from autorfx.analysis.llm import PROMPT_VERSION
from autorfx.analysis.providers import (
    AnalysisProvider,
    CacheStore,
    ClauseInput,
    Judgement,
    TransientProviderError,
    rule_judgement,
)
from autorfx.analysis.translation import translate_clause
from autorfx.jobs import JobReporter, NullReporter
from autorfx.models import Clause, ClauseAnalysis, Finding, Origin, RedFlag
from autorfx.rules import Ruleset

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
SCHEMA_VERSION = 1
Policy = Literal["always", "ambiguous_only", "never"]


class ClauseState(TypedDict, total=False):
    clause: Clause
    candidates: Candidates
    judgement: Judgement
    origin: Origin
    attempt: int
    feedback: str | None
    analysis: ClauseAnalysis


def cache_key(clause: Clause, provider_id: str, ruleset_digest: str) -> str:
    """Key of a cached judgement. Positions are not part of it: a clause that moved still hits."""
    payload = {
        "sentences": [" ".join(unicodedata.normalize("NFC", s.text).split()) for s in clause.sentences],
        "title": clause.own_title,  # what the provider is shown (ClauseInput.of)
        "ruleset": ruleset_digest,
        "provider": provider_id,
        "prompt": PROMPT_VERSION,
        "schema": SCHEMA_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def assemble(
    clause: Clause, candidates: Candidates, judgement: Judgement, origin: Origin, ruleset: Ruleset
) -> ClauseAnalysis:
    """Validate a judgement against the rule files and the clause, and verify its citations."""
    known = set(ruleset.department_ids)
    primary = judgement.primary_department if judgement.primary_department in known else candidates.primary
    wanted = {d for d in judgement.reference_departments if d in known and d != primary}
    reference = tuple(d for d in ruleset.department_ids if d in wanted)
    priority = judgement.priority
    if candidates.priority == "must" and candidates.priority_marker:
        priority = "must"  # a missed obligation is the costly error: an explicit "shall" is never weakened

    count = len(clause.sentences)
    valid = [n for n in judgement.citations if 1 <= n <= count]
    flag_evidence = [n for n in judgement.red_flag_evidence if 1 <= n <= count]
    # Counted before the two lists are merged: valid red-flag evidence must not hide a bad citation.
    cited_nonexistent = len(valid) < len(judgement.citations) or len(flag_evidence) < len(
        judgement.red_flag_evidence
    )
    red_flag: RedFlag | None = None
    rule = next((r for r in ruleset.red_flags if r.type == judgement.red_flag_type), None)
    if rule and flag_evidence:
        red_flag = RedFlag(
            type=rule.type, label=rule.label, reason=judgement.red_flag_reason or rule.description
        )
        valid += flag_evidence

    finding = Finding(
        kind="summary",
        departments=(primary, *reference),
        text=judgement.summary.strip(),
        priority=priority,
        red_flag_reason=red_flag.reason if red_flag else None,
        citations=tuple(clause.sentences[n - 1].locus for n in sorted(set(valid))),
    )
    verified = verify(finding, [clause], check_numbers=origin == "llm")
    if cited_nonexistent:  # the provider cited a sentence that does not exist
        verified = verified.model_copy(update={"citation_status": "unverified"})
    return ClauseAnalysis(
        clause_id=clause.clause_id,
        primary_department=primary,
        reference_departments=reference,
        priority=priority,
        red_flag=red_flag,
        summary=verified,
        origin=origin,
    )


def build_clause_graph(
    provider: AnalysisProvider, ruleset: Ruleset, policy: Policy, cache: CacheStore | None
):
    """Compile the per-clause graph for one provider and one set of rules."""

    def wants_llm(clause: Clause, candidates: Candidates) -> bool:
        if not provider.uses_llm or clause.is_boilerplate or policy == "never":
            return False
        return policy == "always" or candidates.needs_confirm

    def candidates_node(state: ClauseState) -> ClauseState:
        return {"candidates": find_candidates(state["clause"], ruleset), "attempt": 0, "feedback": None}

    def confirm_node(state: ClauseState) -> ClauseState:
        clause, candidates = state["clause"], state["candidates"]
        if not wants_llm(clause, candidates):
            return {"judgement": rule_judgement(candidates), "origin": "rule"}
        if state["attempt"] == 0 and cache is not None:
            cached = cache.get(cache_key(clause, provider.id, ruleset.digest))
            if cached:
                return {"judgement": Judgement.model_validate_json(cached), "origin": "llm", "attempt": 1}
        try:
            judgement = provider.confirm(ClauseInput.of(clause), candidates, state.get("feedback"))
        except TransientProviderError as exc:
            log.warning("provider failed for %s (%s): using the rule result", clause.clause_id, exc)
            return {"judgement": rule_judgement(candidates), "origin": "rule_fallback"}
        return {"judgement": judgement, "origin": "llm", "attempt": state["attempt"] + 1}

    def verify_node(state: ClauseState) -> ClauseState:
        analysis = assemble(
            state["clause"], state["candidates"], state["judgement"], state["origin"], ruleset
        )
        feedback = None
        if analysis.summary.citation_status == "unverified":
            feedback = (
                f"Citations must be sentence numbers between 1 and {len(state['clause'].sentences)}, "
                "and every number in the summary must appear in a cited sentence."
            )
        return {"analysis": analysis, "feedback": feedback}

    def route(state: ClauseState) -> str:
        if state["analysis"].summary.citation_status == "verified" or state["origin"] != "llm":
            return "finalize"
        return "confirm" if state["attempt"] < MAX_ATTEMPTS else "fallback"

    def fallback_node(state: ClauseState) -> ClauseState:
        judgement = rule_judgement(state["candidates"])
        analysis = assemble(state["clause"], state["candidates"], judgement, "rule_fallback", ruleset)
        return {"judgement": judgement, "origin": "rule_fallback", "analysis": analysis}

    def finalize_node(state: ClauseState) -> ClauseState:
        clause, analysis = state["clause"], state["analysis"]
        if state["origin"] == "llm" and analysis.summary.citation_status == "verified" and cache is not None:
            cache.put(cache_key(clause, provider.id, ruleset.digest), state["judgement"].model_dump_json())
        if not provider.translates:
            return {"analysis": analysis.model_copy(update={"translation_status": "no_provider"})}
        if clause.is_boilerplate:
            return {"analysis": analysis.model_copy(update={"translation_status": "boilerplate"})}
        if clause.lang == "ko":
            return {"analysis": analysis.model_copy(update={"translation_status": "source_ko"})}
        result = translate_clause(clause, provider)
        update = {
            "translation_ko": result.text,
            "translation_sentences": result.sentences,
            "translation_status": result.status,
            "translation_note": result.note,
        }
        return {"analysis": analysis.model_copy(update=update)}

    graph = StateGraph(ClauseState)
    graph.add_node("candidates", candidates_node)
    graph.add_node("confirm", confirm_node)
    graph.add_node("verify", verify_node)
    graph.add_node("fallback", fallback_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "candidates")
    graph.add_edge("candidates", "confirm")
    graph.add_edge("confirm", "verify")
    graph.add_conditional_edges(
        "verify", route, {"confirm": "confirm", "fallback": "fallback", "finalize": "finalize"}
    )
    graph.add_edge("fallback", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def analyze(
    clauses: Sequence[Clause],
    ruleset: Ruleset,
    provider: AnalysisProvider,
    *,
    cache: CacheStore | None = None,
    reporter: JobReporter | None = None,
    max_concurrency: int = 4,
    policy: Policy = "ambiguous_only",
) -> tuple[ClauseAnalysis, ...]:
    """One analysis per clause, in input order. Reports the translate, route, assess and verify stages."""
    report = reporter or NullReporter()
    total = len(clauses)
    graph = build_clause_graph(provider, ruleset, policy, cache)
    lock = threading.Lock()
    done = 0

    if provider.translates:
        report.update("translate", "running", f"0/{total} 조항")
    else:
        report.update("translate", "skipped", "번역 제공자가 연결되지 않았습니다.")
    for stage in ("route", "assess", "verify"):
        report.update(stage, "running", f"0/{total} 조항")

    def run(clause: Clause) -> ClauseAnalysis:
        nonlocal done
        result = graph.invoke({"clause": clause})["analysis"]
        with lock:
            done += 1
            if done % 20 == 0 and done < total:
                for stage in ("route", "assess", "verify"):
                    report.update(stage, "running", f"{done}/{total} 조항")
        return result

    try:
        with ThreadPoolExecutor(max_workers=max(1, max_concurrency)) as pool:
            results = tuple(pool.map(run, clauses))
    except Exception as exc:
        report.fail("route", f"분석 중 오류가 발생했습니다 ({type(exc).__name__}).")
        raise

    verified = sum(r.summary.citation_status == "verified" for r in results)
    if provider.translates:
        count = Counter(r.translation_status for r in results)
        excluded = count["boilerplate"] + count["source_ko"]
        report.update(
            "translate",
            "done",
            f"번역 {count['translated']} · 일부 {count['partial']} · 보류 {count['withheld']}"
            f" · 실패 {count['failed']} · 제외 {excluded}",
        )
    report.update("route", "done", f"{total}개 조항 분류")
    must = sum(r.priority == "must" for r in results)
    flags = sum(r.red_flag is not None for r in results)
    report.update("assess", "done", f"필수 {must}개 · 독소조항 후보 {flags}개")
    report.update("verify", "done", f"검증된 인용 {verified}/{total}")
    return results
