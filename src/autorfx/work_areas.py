"""Work areas matched against original RFQ clause text.

A company department ticks the work areas it receives; a clause belongs to every
area whose keywords appear in its text. The areas, the topics they group and their
keywords are in ``data/rules/work_areas.yaml`` (domain-team owned).
"""

from autorfx.rules import WorkAreaRules


def canonical_area(rules: WorkAreaRules, area: str) -> str:
    """The current id for an area id: an earlier fine-grained id (a topic) maps to its area."""
    return next((a.id for a in rules.areas if area in a.topics), area)


def matched_terms(rules: WorkAreaRules, text: str) -> list[tuple[str, str]]:
    """(area id, keyword expression) for every keyword found in ``text``."""
    normalized = " ".join(text.split())
    return [
        (key, pattern.pattern)
        for key, patterns in rules.patterns.items()
        for pattern, _ in patterns
        if pattern.search(normalized)
    ]


def matching_areas(rules: WorkAreaRules, text: str) -> list[str]:
    """Areas whose keywords appear in ``text``, the most specific match first (ties in catalog order)."""
    normalized = " ".join(text.split())
    scores = {
        key: sum(weight for pattern, weight in patterns if pattern.search(normalized))
        for key, patterns in rules.patterns.items()
    }
    return sorted((key for key in scores if scores[key]), key=scores.__getitem__, reverse=True)
