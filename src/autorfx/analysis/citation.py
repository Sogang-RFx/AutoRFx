"""Citation Enforcer (CITE-01, CITE-02, INV-3).

A finding is a normal result only when deterministic code can confirm its
citations. This function is pure: no LLM, no network, no state.

A finding is ``unverified`` when

1. it cites nothing;
2. a cited position does not exist in the given clauses;
3. a cited position lies outside ``allowed`` (the evidence this request was
   permitted to use, e.g. the sentences a restricted team may see);
4. ``check_numbers`` is set and a number in the finding's text appears in none
   of the cited sentences (a generated summary must not carry a figure the
   source does not state).

Whether the cited sentence *means* what the finding says is not checked here;
that is measured by scoring against an answer key.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence

from autorfx.models import Clause, Finding, VerifiedFinding

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
Key = tuple[str, int, int]


def number_signatures(text: str) -> set[str]:
    """Numbers of a text reduced to their digits, so 0.5 % equals 0,5 % and 200,000 equals 200.000."""
    return {re.sub(r"\D", "", match) for match in NUMBER.findall(text)}


def verify(
    finding: Finding,
    clauses: Sequence[Clause],
    *,
    allowed: Collection[Key] | None = None,
    check_numbers: bool = False,
) -> VerifiedFinding:
    """Check the citations of ``finding`` against the sentences of ``clauses``."""
    if not finding.citations:
        return VerifiedFinding(finding=finding, citation_status="unverified")
    known = {s.locus.key(): s.text for clause in clauses for s in clause.sentences}
    failed = tuple(
        locus
        for locus in finding.citations
        if locus.key() not in known or (allowed is not None and locus.key() not in allowed)
    )
    if failed:
        return VerifiedFinding(finding=finding, citation_status="unverified", failed_citations=failed)
    if check_numbers:
        # Every text the finding shows counts, the red-flag reason as much as the summary.
        stated = set().union(*(number_signatures(known[locus.key()]) for locus in finding.citations))
        shown = f"{finding.text} {finding.red_flag_reason or ''}"
        if number_signatures(shown) - stated:
            return VerifiedFinding(
                finding=finding, citation_status="unverified", failed_citations=finding.citations
            )
    return VerifiedFinding(finding=finding, citation_status="verified")
