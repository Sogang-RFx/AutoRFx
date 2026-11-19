"""Rule-based candidates for one clause (ROUTE-01..03, RTM-01, RISK-01).

A pure function of the clause text and the rule files. It decides, without an
LLM: department scores from keywords, the requirement strength from modal
expressions, and red flags from patterns. An LLM, when connected, only
confirms or corrects these candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from autorfx.models import Clause, Model, Priority
from autorfx.rules import Ruleset

STRENGTH_ORDER: tuple[Priority, ...] = ("must", "should", "optional", "info")
NUMBER = re.compile(r"\d[\d.,]*\d|\d")
MAX_REFERENCE = 3
REFERENCE_SHARE = 0.4
TITLE_BONUS = 1


class RedFlagHit(Model):
    type: str
    label: str
    reason: str
    sentence: int


class Candidates(Model):
    primary: str
    reference: tuple[str, ...] = ()
    scores: tuple[tuple[str, int], ...] = ()
    priority: Priority
    priority_marker: str | None = None
    """The expression that decided the strength; None when nothing matched and "info" is a default."""
    red_flag: RedFlagHit | None = None
    evidence: tuple[int, ...]
    """1-based numbers of the clause's sentences that carried the deciding signals."""
    needs_confirm: bool


def term_pattern(term: str) -> str:
    """A rule term as a regular expression: whole words, or the raw expression after "re:"."""
    return term[3:] if term.startswith("re:") else rf"(?<!\w){re.escape(term)}(?!\w)"


@dataclass(frozen=True)
class _Matcher:
    departments: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...]
    strengths: tuple[tuple[Priority, re.Pattern[str]], ...]
    flags: tuple[tuple[str, str, str, re.Pattern[str], re.Pattern[str] | None], ...]
    fallback: str
    boilerplate: str | None


def _alternation(terms: tuple[str, ...]) -> re.Pattern[str] | None:
    return re.compile("|".join(f"(?:{term_pattern(t)})" for t in terms), re.IGNORECASE) if terms else None


def _regex_alternation(patterns: tuple[str, ...]) -> re.Pattern[str] | None:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE) if patterns else None


_MATCHERS: dict[str, _Matcher] = {}


def _matcher_for(ruleset: Ruleset) -> _Matcher:
    """Compiled patterns of a rule set, built once per content digest."""
    matcher = _MATCHERS.get(ruleset.digest)
    if matcher is None:
        if len(_MATCHERS) >= 8:
            _MATCHERS.clear()
        matcher = _MATCHERS[ruleset.digest] = _build_matcher(ruleset)
    return matcher


def _build_matcher(ruleset: Ruleset) -> _Matcher:
    levels = {rule.level: rule for rule in ruleset.priority}
    strengths = []
    for level in STRENGTH_ORDER:
        pattern = _alternation(levels[level].expressions.all())
        if pattern:
            strengths.append((level, pattern))
    flags = []
    for rule in ruleset.red_flags:
        # Red-flag patterns are regular expressions already, unlike keywords and strength terms.
        pattern = _regex_alternation(rule.patterns.all())
        if pattern:
            unless = _regex_alternation(rule.unless.all())
            flags.append((rule.type, rule.label, rule.description, pattern, unless))
    receives_all = next((d.id for d in ruleset.departments if d.receives_all), ruleset.departments[0].id)
    return _Matcher(
        departments=tuple(
            (d.id, tuple(re.compile(term_pattern(k), re.IGNORECASE) for k in d.keywords.all()))
            for d in ruleset.departments
        ),
        strengths=tuple(strengths),
        flags=tuple(flags),
        fallback=receives_all,
        boilerplate=ruleset.sensitivity.boilerplate_department,
    )


def _first_sentence(pattern: re.Pattern[str], sentences: tuple[str, ...]) -> tuple[int, str] | None:
    for number, text in enumerate(sentences, start=1):
        match = pattern.search(text)
        if match:
            return number, match.group(0)
    return None


def find_candidates(clause: Clause, ruleset: Ruleset) -> Candidates:
    """Department, strength and red-flag candidates of a clause."""
    matcher = _matcher_for(ruleset)
    sentences = tuple(s.text for s in clause.sentences)
    text = " ".join(sentences)
    evidence: list[int] = []

    scores: list[tuple[str, int]] = []
    first_hit: dict[str, int] = {}
    for department, keywords in matcher.departments:
        score = 0
        for keyword in keywords:
            if keyword.search(text):
                score += 1
                hit = _first_sentence(keyword, sentences)
                if hit and department not in first_hit:
                    first_hit[department] = hit[0]
            if clause.title and keyword.search(clause.title):
                score += TITLE_BONUS
        scores.append((department, score))
    ranked = sorted(scores, key=lambda item: -item[1])  # stable: ties keep rule-file order
    top_id, top = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0

    if clause.is_boilerplate and matcher.boilerplate:
        primary, reference, close = matcher.boilerplate, (), False
    elif top == 0:
        primary, reference, close = matcher.fallback, (), True
    else:
        primary = top_id
        floor = max(1.0, top * REFERENCE_SHARE)
        chosen = {d for d, s in ranked[1:] if s >= floor and d != matcher.fallback}
        reference = tuple(d for d, _ in scores if d in chosen)[:MAX_REFERENCE]
        close = runner_up >= top - 1 and runner_up > 0
        evidence.append(first_hit.get(primary, 1))

    priority: Priority = "info"
    marker: str | None = None
    # Numbers are blanked first: the dots of "04.12.2026" or "150.000" are not sentence ends.
    blanked = tuple(NUMBER.sub("0", sentence) for sentence in sentences)
    for level, pattern in matcher.strengths:
        hit = _first_sentence(pattern, blanked)
        if hit:
            priority, marker = level, hit[1]
            evidence.append(hit[0])
            break

    red_flag: RedFlagHit | None = None
    for flag_type, label, description, pattern, unless in matcher.flags:
        hit = _first_sentence(pattern, sentences)
        if hit and not (unless and unless.search(text)):
            red_flag = RedFlagHit(
                type=flag_type, label=label, reason=f"{description} 근거 표현: “{hit[1]}”", sentence=hit[0]
            )
            evidence.append(hit[0])
            break

    return Candidates(
        primary=primary,
        reference=reference,
        scores=tuple(scores),
        priority=priority,
        priority_marker=marker,
        red_flag=red_flag,
        evidence=tuple(sorted(set(evidence))) or (1,),
        needs_confirm=not clause.is_boilerplate and (close or marker is None or red_flag is not None),
    )
