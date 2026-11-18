"""Clause chunks (CHUNK-01, CHUNK-02) and their cost sensitivity (RBAC-02).

One clause per section that has text of its own. A clause never crosses a
section boundary. Headings without text name their children and are not
clauses. Text before the first numbered section stays citable as
``front_matter``; each sheet of a workbook is one ``table`` clause.

Whether a clause is cost-sensitive is decided here, by rule, before anything
is indexed or analysed, so that hidden text can be kept out of every later
step (INV-4). The rules are in ``data/rules/sensitivity.yaml``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from autorfx.models import Clause, EntityMention, FileRecord, ParsedFile, ParsedSentence
from autorfx.parser.sentences import detect_language
from autorfx.rules import Ruleset

__all__ = ["apply_cost_entities", "build_clauses", "matches_cost_pattern", "mentions_cost"]


def _any(patterns: Sequence[str]) -> re.Pattern[str] | None:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE) if patterns else None


def _words(terms: Sequence[str]) -> re.Pattern[str] | None:
    return _any([rf"(?<!\w){re.escape(term)}(?!\w)" for term in terms])


def matches_cost_pattern(text: str, ruleset: Ruleset) -> bool:
    """The test a clause's body text gets (titles are judged separately). Used on PDF page text."""
    pattern = _any(ruleset.sensitivity.cost_patterns.all())
    return bool(pattern and pattern.search(text))


def mentions_cost(text: str, ruleset: Ruleset) -> bool:
    """Whether ``text`` matches a cost pattern or names a cost title. Used for questions (RAG-04)."""
    rules = ruleset.sensitivity
    matchers = (_any(rules.cost_patterns.all()), _words(rules.cost_titles.all()))
    return any(matcher is not None and matcher.search(text) for matcher in matchers)


def build_clauses(parsed: ParsedFile, file: FileRecord, ruleset: Ruleset) -> tuple[Clause, ...]:
    """Clauses of one file, in document order."""
    rules = ruleset.sensitivity
    cost_text = _any(rules.cost_patterns.all())
    cost_title = _words(rules.cost_titles.all())
    boilerplate = _any(rules.boilerplate_patterns)
    by_section: dict[str, list[ParsedSentence]] = defaultdict(list)
    for sentence in parsed.sentences:
        by_section[sentence.locus.section_id].append(sentence)

    clauses: list[Clause] = []
    for section in parsed.sections:
        sentences = by_section.get(section.section_id)
        if not sentences or section.heading_only:
            continue
        text = " ".join(s.text for s in sentences)
        lang = detect_language(text)
        clause = Clause(
            clause_id=f"{file.id}:{section.section_id}",
            source_doc=file.id,
            section_id=section.section_id,
            section_path=section.path or (section.section_id,),
            title=section.title,
            kind=section.kind,
            lang=parsed.lang if lang == "unknown" else lang,
            sentences=tuple(sentences),
            is_boilerplate=bool(
                boilerplate and (boilerplate.search(section.section_id) or boilerplate.search(text[:80]))
            ),
        )
        # Only the clause's own title counts: logistics must see the delivery-term clause
        # although its chapter is called "Prices and terms".
        is_cost = (cost_text and cost_text.search(text)) or (
            cost_title and cost_title.search(clause.own_title)
        )
        clauses.append(clause.model_copy(update={"is_cost_sensitive": True}) if is_cost else clause)
    return tuple(clauses)


def apply_cost_entities(
    clauses: Sequence[Clause], mentions: Sequence[EntityMention], ruleset: Ruleset
) -> tuple[Clause, ...]:
    """Clauses, with those that state a cost entity (target price, …) marked cost-sensitive too."""
    cost_entities = set(ruleset.sensitivity.cost_entities)
    hits = {m.locus.key() for m in mentions if m.entity in cost_entities}
    if not hits:
        return tuple(clauses)
    return tuple(
        clause.model_copy(update={"is_cost_sensitive": True})
        if not clause.is_cost_sensitive and any(s.locus.key() in hits for s in clause.sentences)
        else clause
        for clause in clauses
    )
