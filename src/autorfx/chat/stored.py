"""Answers from stored results, without an LLM (INV-3, INV-8, INV-12, docs/design/chat.md §3.4).

What this group may not see is taken out first. A hidden item leaves no reason, no label and no
count: the reply is the reply of a package that never had it.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from autorfx.access import Key, visible_keys, withheld_ids
from autorfx.chat.models import ChatContext, CitedText
from autorfx.models import ConsistencyIssue, Group, Locus

SEVERITIES = ("high", "medium", "low")


def citable_keys(context: ChatContext, group: Group) -> frozenset[Key]:
    """Positions this group may be shown: an allowlist, so an unknown group gets the reduced one."""
    if group == "cost_visible":
        return frozenset(s.locus.key() for c in context.clauses for s in c.sentences)
    return visible_keys(context.clauses)


def _withheld(context: ChatContext, group: Group) -> frozenset[str]:
    return withheld_ids(context.clauses, context.analyses, context.issues, group=group)


def visible_issues(context: ChatContext, group: Group) -> tuple[ConsistencyIssue, ...]:
    withheld = _withheld(context, group)
    return tuple(issue for issue in context.issues if issue.id not in withheld)


def _citations(loci: Iterable[Locus], known: frozenset[Key]) -> tuple[Locus, ...]:
    """Without repeats, and without positions that are in no clause and so cannot be opened (INV-3)."""
    return tuple({locus.key(): locus for locus in loci if locus.key() in known}.values())


def red_flag_statements(context: ChatContext, group: Group) -> tuple[CitedText, ...]:
    """One statement per verified red flag, in document order."""
    withheld, known = _withheld(context, group), citable_keys(context, group)
    by_id = {a.clause_id: a for a in context.analyses}
    found: list[CitedText] = []
    for clause in context.clauses:
        analysis = by_id.get(clause.clause_id)
        if analysis is None or analysis.red_flag is None or clause.clause_id in withheld:
            continue
        if analysis.summary.citation_status != "verified":
            continue
        if cited := _citations(analysis.summary.finding.citations, known):
            flag = analysis.red_flag
            found.append(CitedText(text=f"{flag.label}: {flag.reason}", citations=cited))
    return tuple(found)


def conflict_statements(context: ChatContext, group: Group) -> tuple[CitedText, ...]:
    """Every issue of every category, most severe first."""
    known = citable_keys(context, group)
    ordered = sorted(visible_issues(context, group), key=lambda i: SEVERITIES.index(i.severity))
    cited = ((issue, _citations((e.locus for e in issue.evidence), known)) for issue in ordered)
    return tuple(CitedText(text=issue.title, citations=loci) for issue, loci in cited if loci)


def related_conflicts(cited_keys: Collection[Key], context: ChatContext, group: Group) -> tuple[dict, ...]:
    """Contract ``conflicts``: the visible contradictions that a cited sentence is evidence of."""
    known = citable_keys(context, group)
    found: list[dict] = []
    for issue in visible_issues(context, group):
        loci = _citations((e.locus for e in issue.evidence), known)
        if issue.category != "conflict" or not any(locus.key() in cited_keys for locus in loci):
            continue
        citations = [locus.model_dump(mode="json") for locus in loci]
        found.append({"id": issue.id, "title": issue.title, "detail": issue.detail, "citations": citations})
    return tuple(found)
