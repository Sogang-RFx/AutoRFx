"""Sentence-by-sentence translation of a clause (TRANS-01..03, CITE-02).

A clause is sent in batches of sentences. Every sentence is judged on its own: a
number that the same source sentence lacks withholds that sentence only, and a
sentence the model did not return is asked for once more. Notes carry counts
and exception names, never clause or translation text.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Literal, NamedTuple

from autorfx.analysis.citation import number_signatures
from autorfx.analysis.providers import (
    AnalysisProvider,
    ClauseInput,
    SentenceTranslation,
    TransientProviderError,
    TranslationBatch,
)
from autorfx.analysis.usage import Usage
from autorfx.models import Clause, Model

__all__ = [
    "WITHHELD",
    "ClauseTranslation",
    "SentenceTranslation",
    "TranslationBatch",
    "batches",
    "clean",
    "translate_clause",
]

log = logging.getLogger(__name__)

WITHHELD = "〔번역 보류: 원문 확인〕"
MAX_BATCH_SENTENCES = 20
MAX_BATCH_CHARS = 4000
MARKER = re.compile(r"\s*\[S\d+\]\s*")
GAP = "\x00"  # stands where a removed marker was, until the spacing around it is settled
GAP_RUN = re.compile(GAP + "+")
GAP_EDGE = re.compile(f"^{GAP}|{GAP}(?=[.,;:!?。、)]|$)")

Outcome = Literal["ok", "withheld", "failed"]


class ClauseTranslation(Model):
    status: Literal["translated", "partial", "withheld", "failed"]
    sentences: tuple[str | None, ...]
    note: str = ""
    usage: Usage = Usage()

    @property
    def text(self) -> str | None:
        """The sentences joined; a missing one is shown as WITHHELD. None when none was translated."""
        if all(s is None for s in self.sentences):
            return None
        return " ".join(WITHHELD if s is None else s for s in self.sentences)


class Fetched(NamedTuple):
    """What the provider returned for a clause, before any sentence is judged."""

    texts: dict[int, str]
    missing: int
    errors: tuple[str, ...]
    usage: Usage


class Judged(NamedTuple):
    sentences: tuple[str | None, ...]
    outcomes: tuple[Outcome, ...]
    extra_numbers: int


def batches(sentences: Sequence[str]) -> list[tuple[int, ...]]:
    """Sentence numbers (from 1) grouped into requests: at most 20 sentences and 4,000 characters."""
    result: list[tuple[int, ...]] = []
    current: list[int] = []
    size = 0
    for n, text in enumerate(sentences, start=1):
        if current and (len(current) >= MAX_BATCH_SENTENCES or size + len(text) > MAX_BATCH_CHARS):
            result.append(tuple(current))
            current, size = [], 0
        current.append(n)
        size += len(text)
    if current:
        result.append(tuple(current))
    return result


def clean(text: str, source: str) -> str:
    """Remove copied "[S<n>]" markers, unless that exact marker is part of the source sentence.

    The text around a removed marker is joined with one space, or with none at an end or before punctuation.
    """

    def drop(match: re.Match[str]) -> str:
        return match.group(0) if match.group(0).strip() in source else GAP

    text = GAP_EDGE.sub("", GAP_RUN.sub(GAP, MARKER.sub(drop, text)))
    return text.replace(GAP, " ").strip()


def _accept(batch: TranslationBatch, wanted: set[int], sources: Sequence[str]) -> dict[int, str]:
    """The cleaned text per requested number.

    The first occurrence of a number is the answer: a later duplicate never replaces it, so a number whose
    first text is blank (also after marker cleaning) is absent. Unrequested numbers are dropped.
    """
    got: dict[int, str] = {}
    seen: set[int] = set()
    for item in batch.items:
        if item.n not in wanted or item.n in seen:
            continue
        seen.add(item.n)
        text = clean(item.text, sources[item.n - 1])
        if text:
            got[item.n] = text
    return got


def _fetch(source: ClauseInput, provider: AnalysisProvider) -> Fetched:
    """Ask for every batch, and once more for numbers a batch left out. Only transient errors are caught."""
    texts: dict[int, str] = {}
    errors: list[str] = []
    usage = Usage()
    missing = 0
    for numbers in batches(source.sentences):
        try:
            reply, used = provider.translate(source, numbers)
        except TransientProviderError as exc:
            errors.append(str(exc))
            usage = usage.plus(exc.usage or Usage())
            continue
        usage = usage.plus(used)
        got = _accept(reply, set(numbers), source.sentences)
        again = tuple(n for n in numbers if n not in got)
        if again:
            try:
                reply, used = provider.translate(source, again)
            except TransientProviderError as exc:
                errors.append(str(exc))
                usage = usage.plus(exc.usage or Usage())
            else:
                usage = usage.plus(used)
                got |= _accept(reply, set(again), source.sentences)
                missing += sum(1 for n in again if n not in got)
        texts |= got
    return Fetched(texts, missing, tuple(dict.fromkeys(errors)), usage)


def _judge(source: ClauseInput, texts: dict[int, str]) -> Judged:
    """Check each fetched sentence against its own source sentence (CITE-02)."""
    sentences: list[str | None] = []
    outcomes: list[Outcome] = []
    extra_total = 0
    for n, original in enumerate(source.sentences, start=1):
        text = texts.get(n)
        # A translation is shown next to the original: a number that the sentence lacks is an error
        # of the translator, and that sentence is withheld rather than shown.
        extra = len(number_signatures(text or "") - number_signatures(original))
        if text is None:
            sentences.append(None)
            outcomes.append("failed")
        elif extra:
            sentences.append(None)
            outcomes.append("withheld")
            extra_total += extra
        else:
            sentences.append(text)
            outcomes.append("ok")
    return Judged(tuple(sentences), tuple(outcomes), extra_total)


def _status(outcomes: Sequence[Outcome]) -> Literal["translated", "partial", "withheld", "failed"]:
    if all(o == "ok" for o in outcomes):
        return "translated"
    if "ok" in outcomes:
        return "partial"
    if all(o == "withheld" for o in outcomes):
        return "withheld"
    return "failed"


def translate_clause(clause: Clause, provider: AnalysisProvider) -> ClauseTranslation:
    source = ClauseInput.of(clause)
    fetched = _fetch(source, provider)
    judged = _judge(source, fetched.texts)
    status = _status(judged.outcomes)
    counts = [f"extra_numbers={judged.extra_numbers}"] if judged.extra_numbers else []
    counts += [f"missing={fetched.missing}"] if fetched.missing else []
    note = "; ".join([*counts, *fetched.errors])
    for name in fetched.errors:
        log.warning("translation of %s failed (%s)", clause.clause_id, name)
    if status != "translated":
        log.warning("translation of %s is %s (%s)", clause.clause_id, status, note)
    return ClauseTranslation(status=status, sentences=judged.sentences, note=note, usage=fetched.usage)
