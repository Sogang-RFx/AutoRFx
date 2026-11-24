"""Sentence-by-sentence translation of a clause (TRANS-01..03, CITE-02).

A clause is sent in batches of sentences. Every sentence is judged on its own: a
number that the same source sentence lacks withholds that sentence only, and a
sentence the model did not return is asked for once more. After two failed
first calls in a row the rest of the clause is not asked. Notes carry counts
and exception names, never clause or translation text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import unicodedata
from collections.abc import Sequence
from typing import Literal, NamedTuple

from autorfx.analysis.citation import number_signatures
from autorfx.analysis.llm import TRANSLATION_PROMPT_VERSION
from autorfx.analysis.providers import (
    AnalysisProvider,
    CacheStore,
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
    "is_source_korean",
    "translate_clause",
    "translation_key",
]

log = logging.getLogger(__name__)

WITHHELD = "〔번역 보류: 원문 확인〕"
MAX_BATCH_SENTENCES = 20
MAX_BATCH_CHARS = 4000
MAX_CONSECUTIVE_FAILURES = 2  # failed first calls in a row before the rest of a clause is not asked
HANGUL = re.compile("[\uac00-\ud7a3]")
LATIN = re.compile("[A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f]")
CACHE_SCHEMA = 1  # shape of a cached value: a JSON list of texts in batch order
MARKER = re.compile(r"[ \t]*\[S\d+\][ \t]*")  # spaces and tabs only: a line break is part of the text
LINE_BREAK = re.compile(r" *\n *")
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
    skipped: int = 0


class Judged(NamedTuple):
    sentences: tuple[str | None, ...]
    outcomes: tuple[Outcome, ...]
    extra_numbers: int


def is_source_korean(clause: Clause) -> bool:
    """True when the clause is labelled Korean and Hangul is at least half of its letters (Hangul + Latin)."""
    if clause.lang != "ko":
        return False
    hangul = len(HANGUL.findall(clause.text))
    latin = len(LATIN.findall(clause.text))
    return hangul > 0 and hangul * 2 >= hangul + latin


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
    Line breaks stay, without spaces around them.
    """

    def drop(match: re.Match[str]) -> str:
        return match.group(0) if match.group(0).strip() in source else GAP

    text = GAP_EDGE.sub("", GAP_RUN.sub(GAP, MARKER.sub(drop, text)))
    return LINE_BREAK.sub("\n", text.replace(GAP, " ")).strip()


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


def translation_key(sentences: Sequence[str], lang: str, translation_id: str, terms_digest: str) -> str:
    """Key of one cached batch: its sentences in order, not its clause or numbers, so a moved batch hits.

    The rule set's digest is not part of it: a rule change does not change a translation.
    """
    payload = {
        "kind": "translation",
        "sentences": [" ".join(unicodedata.normalize("NFC", s).split()) for s in sentences],
        "lang": lang,
        "engine": translation_id,
        "terms": terms_digest,
        "prompt": TRANSLATION_PROMPT_VERSION,
        "schema": CACHE_SCHEMA,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _stored(cache: CacheStore, key: str, size: int, clause_id: str) -> list[str] | None:
    """The stored texts, or None unless the value is a JSON list of exactly ``size`` non-blank strings.

    A cache that fails to read is a miss. Only ``sqlite3.Error`` is caught (the cache is a sqlite file that
    can be locked or full): a bug elsewhere must not hide behind it. The paid translation goes on.
    """
    try:
        raw = cache.get(key)
    except sqlite3.Error as exc:
        log.warning("translation cache read for %s failed (%s)", clause_id, type(exc).__name__)
        return None
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, list) or len(value) != size:
        return None
    if not all(isinstance(t, str) and t.strip() for t in value):
        return None
    return value


def _extra_numbers(text: str, original: str) -> int:
    """How many numbers the text has that its source sentence lacks (CITE-02)."""
    return len(number_signatures(text) - number_signatures(original))


def _verified(text: str | None, source: ClauseInput, n: int) -> bool:
    return text is not None and not _extra_numbers(text, source.sentences[n - 1])


def _store(cache: CacheStore, key: str, texts: list[str], clause_id: str) -> None:
    """Store a verified batch. A write that fails is skipped (see ``_stored``); the translation stays."""
    try:
        cache.put(key, json.dumps(texts, ensure_ascii=False))
    except sqlite3.Error as exc:
        log.warning("translation cache write for %s failed (%s)", clause_id, type(exc).__name__)


def _fetch(
    source: ClauseInput,
    provider: AnalysisProvider,
    cache: CacheStore | None = None,
    terms_digest: str = "",
) -> Fetched:
    """Ask for every batch, and once more for numbers a batch left out. Only transient errors are caught.

    A batch with a stored translation is not asked for. A batch is stored only when every one of its
    numbers has a text that passes the number check (verified results only, like the judgement cache).
    After MAX_CONSECUTIVE_FAILURES failed first calls in a row the remaining batches are not asked (stored
    ones are still read); their sentences count as skipped. A retry that fails does not count.
    """
    texts: dict[int, str] = {}
    errors: list[str] = []
    usage = Usage()
    missing = 0
    skipped = 0
    failures = 0
    translation_id = provider.translation_id
    for numbers in batches(source.sentences):
        key = None
        if cache is not None and translation_id is not None:
            key = translation_key(
                [source.sentences[n - 1] for n in numbers], source.lang, translation_id, terms_digest
            )
            stored = _stored(cache, key, len(numbers), source.clause_id)
            if stored is not None:
                texts |= dict(zip(numbers, stored, strict=True))
                continue
        if failures >= MAX_CONSECUTIVE_FAILURES:
            skipped += len(numbers)
            continue
        try:
            reply, used = provider.translate(source, numbers)
        except TransientProviderError as exc:
            errors.append(str(exc))
            usage = usage.plus(exc.usage or Usage())
            failures += 1
            continue
        failures = 0
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
        if cache is not None and key is not None and all(_verified(got.get(n), source, n) for n in numbers):
            _store(cache, key, [got[n] for n in numbers], source.clause_id)
        texts |= got
    return Fetched(texts, missing, tuple(dict.fromkeys(errors)), usage, skipped)


def _judge(source: ClauseInput, texts: dict[int, str]) -> Judged:
    """Check each fetched sentence against its own source sentence (CITE-02)."""
    sentences: list[str | None] = []
    outcomes: list[Outcome] = []
    extra_total = 0
    for n, original in enumerate(source.sentences, start=1):
        text = texts.get(n)
        # A translation is shown next to the original: a number that the sentence lacks is an error
        # of the translator, and that sentence is withheld rather than shown.
        extra = _extra_numbers(text or "", original)
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


def translate_clause(
    clause: Clause,
    provider: AnalysisProvider,
    *,
    cache: CacheStore | None = None,
    terms_digest: str = "",
) -> ClauseTranslation:
    source = ClauseInput.of(clause)
    fetched = _fetch(source, provider, cache, terms_digest)
    judged = _judge(source, fetched.texts)
    status = _status(judged.outcomes)
    counts = [f"extra_numbers={judged.extra_numbers}"] if judged.extra_numbers else []
    counts += [f"missing={fetched.missing}"] if fetched.missing else []
    counts += [f"skipped={fetched.skipped}"] if fetched.skipped else []
    note = "; ".join([*counts, *fetched.errors])
    for name in fetched.errors:
        log.warning("translation of %s failed (%s)", clause.clause_id, name)
    if status != "translated":
        log.warning("translation of %s is %s (%s)", clause.clause_id, status, note)
    return ClauseTranslation(status=status, sentences=judged.sentences, note=note, usage=fetched.usage)
