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
from typing import TYPE_CHECKING, Literal

from autorfx.analysis.citation import number_signatures
from autorfx.analysis.providers import ClauseInput, TransientProviderError
from autorfx.analysis.usage import Usage
from autorfx.models import Clause, Model

if TYPE_CHECKING:
    from autorfx.analysis.providers import AnalysisProvider

log = logging.getLogger(__name__)

WITHHELD = "〔번역 보류: 원문 확인〕"
MAX_BATCH_SENTENCES = 20
MAX_BATCH_CHARS = 4000
MARKER = re.compile(r"\s*\[S\d+\]\s*")

Outcome = Literal["ok", "withheld", "failed"]


class SentenceTranslation(Model):
    n: int
    text: str


class TranslationBatch(Model):
    items: tuple[SentenceTranslation, ...] = ()


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
    """Remove copied "[S<n>]" markers, unless that exact marker is part of the source sentence."""

    def drop(match: re.Match[str]) -> str:
        return match.group(0) if match.group(0).strip() in source else " "

    return MARKER.sub(drop, text).strip()


def _accept(batch: TranslationBatch, wanted: set[int], got: dict[int, str]) -> None:
    """Keep the first non-blank text per requested number; drop duplicates and unrequested numbers."""
    seen: set[int] = set()
    for item in batch.items:
        if item.n not in wanted or item.n in seen:
            continue
        seen.add(item.n)
        if item.n not in got and item.text.strip():
            got[item.n] = item.text


def translate_clause(clause: Clause, provider: AnalysisProvider) -> ClauseTranslation:
    source = ClauseInput.of(clause)
    total = len(source.sentences)
    raw: dict[int, str] = {}
    usage = Usage()
    missing = 0
    errors: list[str] = []

    def note_error(exc: TransientProviderError) -> None:
        log.warning("translation of %s failed (%s)", clause.clause_id, exc)
        if str(exc) not in errors:
            errors.append(str(exc))

    for numbers in batches(source.sentences):
        wanted = set(numbers)
        got: dict[int, str] = {}
        try:
            reply, used = provider.translate(source, numbers)
        except TransientProviderError as exc:
            note_error(exc)
            continue
        usage = usage.plus(used)
        _accept(reply, wanted, got)
        again = tuple(n for n in numbers if n not in got)
        if again:
            try:
                reply, used = provider.translate(source, again)
            except TransientProviderError as exc:
                note_error(exc)
            else:
                usage = usage.plus(used)
                _accept(reply, set(again), got)
                missing += sum(1 for n in again if n not in got)
        raw.update(got)

    sentences: list[str | None] = []
    outcomes: list[Outcome] = []
    extra_total = 0
    for n in range(1, total + 1):
        if n not in raw:
            sentences.append(None)
            outcomes.append("failed")
            continue
        text = clean(raw[n], source.sentences[n - 1])
        extra = len(number_signatures(text) - number_signatures(source.sentences[n - 1]))
        if not text:
            sentences.append(None)
            outcomes.append("failed")
            missing += 1
        # A translation is shown next to the original: a number that the sentence lacks is an error
        # of the translator, and that sentence is withheld rather than shown (CITE-02).
        elif extra:
            sentences.append(None)
            outcomes.append("withheld")
            extra_total += extra
        else:
            sentences.append(text)
            outcomes.append("ok")

    if all(o == "ok" for o in outcomes):
        status = "translated"
    elif "ok" in outcomes:
        status = "partial"
    elif all(o == "withheld" for o in outcomes):
        status = "withheld"
    else:
        status = "failed"
    parts = ([f"extra_numbers={extra_total}"] if extra_total else []) + (
        [f"missing={missing}"] if missing else []
    )
    note = "; ".join(parts + errors)
    if status != "translated":
        log.warning("translation of %s is %s (%s)", clause.clause_id, status, note)
    return ClauseTranslation(status=status, sentences=tuple(sentences), note=note, usage=usage)
