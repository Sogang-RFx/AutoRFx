"""Analysis providers (ENGINE-01, ENGINE-02) and the result cache.

The pipeline talks to one interface. ``RuleOnlyProvider`` needs no key and is
the default; an LLM provider (see :mod:`autorfx.analysis.llm`) is used when a
key is configured; ``ScriptedProvider`` drives tests.

A provider never states a position. It receives sentences numbered from 1 and
cites by number; code maps numbers to positions (CITE-01).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from autorfx.analysis.candidates import Candidates
from autorfx.analysis.usage import Usage
from autorfx.models import Clause, Lang, Locus, Model, Priority

MAX_EXTRACT = 3


class TransientProviderError(RuntimeError):
    """The provider could not answer right now (timeout, rate limit). The caller falls back to rules.

    The message is an exception name only, never text of a clause or an answer: it is stored and logged.
    ``usage`` is what a failed call still cost (a reply that did not parse was billed).
    """

    def __init__(self, message: str = "", *, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class ClauseInput(Model):
    clause_id: str
    title: str
    lang: Lang
    sentences: tuple[str, ...]

    @classmethod
    def of(cls, clause: Clause) -> ClauseInput:
        return cls(
            clause_id=clause.clause_id,
            # Only the clause's own title: an inherited chapter heading may state a price that a
            # restricted team must not get back through the summary or translation (INV-8).
            title=clause.own_title,
            lang=clause.lang,
            sentences=tuple(s.text for s in clause.sentences),
        )


class Judgement(Model):
    """What a provider says about a clause. Citations are sentence numbers, from 1."""

    primary_department: str
    reference_departments: tuple[str, ...] = ()
    priority: Priority
    red_flag_type: str | None = None
    red_flag_reason: str | None = None
    red_flag_evidence: tuple[int, ...] = ()
    summary: str = ""
    citations: tuple[int, ...] = ()


class EvidenceSentence(Model):
    number: int
    text: str
    locus: Locus


class Answer(Model):
    text: str
    citations: tuple[int, ...]
    answered: bool
    """False when the evidence does not answer the question. Required, with no default, so the
    model always states it (and strict structured output accepts the schema)."""


class SentenceTranslation(Model):
    """One translated sentence, by the 1-based number it was sent with."""

    n: int
    text: str


class TranslationBatch(Model):
    items: tuple[SentenceTranslation, ...]
    """Required, with no default: a malformed reply is a schema error, and a strict schema has no defaults."""


class SheetMap(Model):
    """Where the items of a table are: the header row and the column of each role (None when absent).

    Only positions: the reading itself (every name and number) is done by code from the cells.
    Every field is required (nullable), so strict structured output accepts the schema.
    """

    is_price_sheet: bool
    """False when the table lists no items to buy (notes, exchange rates, a signature block)."""
    header_row: int | None
    no: int | None
    part: int | None
    description: int | None
    spec: int | None
    quantity: int | None
    unit: int | None
    months: int | None
    price: int | None
    total: int | None
    reason: str
    """Why, in Korean, in one sentence."""


class AnalysisProvider(Protocol):
    id: str
    uses_llm: bool
    translates: bool
    translation_id: str | None
    """Engine that translates, None when nothing is translated."""

    def translate(self, clause: ClauseInput, numbers: Sequence[int]) -> tuple[TranslationBatch, Usage]: ...

    def confirm(self, clause: ClauseInput, candidates: Candidates, feedback: str | None) -> Judgement: ...

    def answer(self, question: str, evidence: Sequence[EvidenceSentence], feedback: str | None) -> Answer: ...

    def map_sheet(self, sample: str) -> SheetMap | None: ...


def rule_judgement(candidates: Candidates) -> Judgement:
    """The rule candidates as a judgement. There is no summary: nothing wrote one."""
    flag = candidates.red_flag
    return Judgement(
        primary_department=candidates.primary,
        reference_departments=candidates.reference,
        priority=candidates.priority,
        red_flag_type=flag.type if flag else None,
        red_flag_reason=flag.reason if flag else None,
        red_flag_evidence=(flag.sentence,) if flag else (),
        summary="",
        citations=candidates.evidence,
    )


class RuleOnlyProvider:
    """No LLM: judgements are the rule candidates, answers are original sentences, nothing is translated."""

    id = "rule-only"
    uses_llm = False
    translates = False
    translation_id = None

    def translate(self, clause: ClauseInput, numbers: Sequence[int]) -> tuple[TranslationBatch, Usage]:
        return TranslationBatch(items=()), Usage()

    def confirm(self, clause: ClauseInput, candidates: Candidates, feedback: str | None) -> Judgement:
        return rule_judgement(candidates)

    def answer(self, question: str, evidence: Sequence[EvidenceSentence], feedback: str | None) -> Answer:
        top = list(evidence)[:MAX_EXTRACT]
        return Answer(
            text="\n".join(e.text for e in top), citations=tuple(e.number for e in top), answered=True
        )

    def map_sheet(self, sample: str) -> SheetMap | None:
        return None


class ScriptedProvider:
    """Test double: returns queued judgements per clause and records every call."""

    uses_llm = True

    def __init__(
        self,
        script: dict[str, list[Judgement | Exception]] | None = None,
        *,
        answers: list[Answer | Exception] | None = None,
        sheet_maps: list[SheetMap | Exception] | None = None,
        translation: str | None = None,
        translations: dict[str, list[dict[int, str] | str | TranslationBatch | Exception]] | None = None,
        provider_id: str = "scripted",
        usage: Usage | None = None,
    ) -> None:
        self.id = provider_id
        self.translates = translation is not None or translations is not None
        self.translation_id = provider_id if self.translates else None
        self._usage = usage or Usage()
        self._translations = {key: list(value) for key, value in (translations or {}).items()}
        self.translate_calls: list[tuple[str, tuple[int, ...]]] = []
        self._script = {key: list(value) for key, value in (script or {}).items()}
        self._answers = list(answers or [])
        self._sheet_maps = list(sheet_maps or [])
        self.sheet_calls: list[str] = []
        self._translation = translation
        self._lock = threading.Lock()
        self.confirm_calls: list[tuple[str, str | None]] = []
        self.answer_calls: list[tuple[str, tuple[str, ...], str | None]] = []

    def translate(self, clause: ClauseInput, numbers: Sequence[int]) -> tuple[TranslationBatch, Usage]:
        """A queued dict or batch is returned as it is (requested or not); a text answers every number."""
        with self._lock:
            self.translate_calls.append((clause.clause_id, tuple(numbers)))
            queue = self._translations.get(clause.clause_id)
            item = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else self._translation
        if isinstance(item, Exception):
            raise item
        if isinstance(item, TranslationBatch):
            return item, self._usage
        if isinstance(item, dict):
            return TranslationBatch(
                items=tuple(SentenceTranslation(n=n, text=t) for n, t in item.items())
            ), self._usage
        if item is None:
            return TranslationBatch(items=()), self._usage
        batch = TranslationBatch(items=tuple(SentenceTranslation(n=n, text=item) for n in numbers))
        return batch, self._usage

    def confirm(self, clause: ClauseInput, candidates: Candidates, feedback: str | None) -> Judgement:
        with self._lock:
            self.confirm_calls.append((clause.clause_id, feedback))
            queue = self._script.get(clause.clause_id)
            item = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else rule_judgement(candidates)
        if isinstance(item, Exception):
            raise item
        return item

    def answer(self, question: str, evidence: Sequence[EvidenceSentence], feedback: str | None) -> Answer:
        with self._lock:
            self.answer_calls.append((question, tuple(e.text for e in evidence), feedback))
            item = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(item, Exception):
            raise item
        return item

    def map_sheet(self, sample: str) -> SheetMap | None:
        with self._lock:
            self.sheet_calls.append(sample)
            item = self._sheet_maps.pop(0) if len(self._sheet_maps) > 1 else (self._sheet_maps or [None])[0]
        if isinstance(item, Exception):
            raise item
        return item


# ── cache ───────────────────────────────────────────────────────────────────
class CacheStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def put(self, key: str, value: str) -> None: ...


class MemoryCache:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._data.get(key)

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value


class SqliteCache:
    """Results keyed by content hash.

    Judgement values hold sentence numbers, never document text. The translation cache file
    (``translation.sqlite``) does hold translated customer text. It stays in the git-ignored cache folder and
    is not removed when an RFQ is re-analysed (a later run reads it) or deleted: the application has no RFQ
    deletion, and no code removes cache files. Delete the file by hand to forget the translations.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock, self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO cache (key, value) VALUES (?, ?)", (key, value))

    def close(self) -> None:
        with self._lock:
            self._db.close()
