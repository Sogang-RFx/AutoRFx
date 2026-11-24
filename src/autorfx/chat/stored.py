"""Answers from stored results, without an LLM (INV-3, INV-8, INV-12, docs/design/chat.md §3.4).

What this group may not see is taken out first. A hidden item leaves no reason, no label and no
count: the reply is the reply of a package that never had it.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from autorfx import access
from autorfx.access import Key, visible_keys
from autorfx.auth.policy import Scope
from autorfx.chat.models import ChatContext, CitedText
from autorfx.models import Clause, ConsistencyIssue, Locus

SEVERITIES = ("high", "medium", "low")


def _visible_clauses(context: ChatContext, scope: Scope) -> tuple[Clause, ...]:
    """Defense in depth: even if a caller passed a cost clause to a cost-restricted scope, drop it."""
    if scope.cost_visible:
        return tuple(context.clauses)
    return tuple(c for c in context.clauses if not c.is_cost_sensitive)


def citable_keys(context: ChatContext, scope: Scope) -> frozenset[Key]:
    """Positions this scope may be shown: an allowlist of the visible clauses."""
    return visible_keys(_visible_clauses(context, scope))


def visible_issues(context: ChatContext, scope: Scope) -> tuple[ConsistencyIssue, ...]:
    return access.visible_issues(context.issues, scope=scope, visible=_visible_clauses(context, scope))


def _citations(loci: Iterable[Locus], known: frozenset[Key]) -> tuple[Locus, ...]:
    """Without repeats, and without positions that are in no clause and so cannot be opened (INV-3)."""
    return tuple({locus.key(): locus for locus in loci if locus.key() in known}.values())


def red_flag_statements(context: ChatContext, scope: Scope) -> tuple[CitedText, ...]:
    """One statement per verified red flag in visible clauses, in document order."""
    visible = _visible_clauses(context, scope)
    known = visible_keys(visible)
    by_id = {a.clause_id: a for a in context.analyses}
    found: list[CitedText] = []
    for clause in visible:
        analysis = by_id.get(clause.clause_id)
        if analysis is None or analysis.red_flag is None:
            continue
        if analysis.summary.citation_status != "verified":
            continue
        if cited := _citations(analysis.summary.finding.citations, known):
            flag = analysis.red_flag
            found.append(CitedText(text=f"{flag.label}: {flag.reason}", citations=cited))
    return tuple(found)


def conflict_statements(context: ChatContext, scope: Scope) -> tuple[CitedText, ...]:
    """Every visible issue of every category, most severe first."""
    known = citable_keys(context, scope)
    ordered = sorted(visible_issues(context, scope), key=lambda i: SEVERITIES.index(i.severity))
    cited = ((issue, _citations((e.locus for e in issue.evidence), known)) for issue in ordered)
    return tuple(CitedText(text=issue.title, citations=loci) for issue, loci in cited if loci)


def related_conflicts(cited_keys: Collection[Key], context: ChatContext, scope: Scope) -> tuple[dict, ...]:
    """Contract ``conflicts``: the visible contradictions that a cited sentence is evidence of."""
    known = citable_keys(context, scope)
    found: list[dict] = []
    for issue in visible_issues(context, scope):
        loci = _citations((e.locus for e in issue.evidence), known)
        if issue.category != "conflict" or not any(locus.key() in cited_keys for locus in loci):
            continue
        citations = [locus.model_dump(mode="json") for locus in loci]
        found.append({"id": issue.id, "title": issue.title, "detail": issue.detail, "citations": citations})
    return tuple(found)
