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
from pathlib import Path
from typing import Protocol

from autorfx.analysis.candidates import Candidates
from autorfx.models import Clause, Lang, Model, Priority


class TransientProviderError(RuntimeError):
    """The provider could not answer right now (timeout, rate limit). The caller falls back to rules."""


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


class AnalysisProvider(Protocol):
    id: str
    uses_llm: bool
    translates: bool

    def translate(self, clause: ClauseInput) -> str | None: ...

    def confirm(self, clause: ClauseInput, candidates: Candidates, feedback: str | None) -> Judgement: ...


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
    """No LLM: judgements are the rule candidates, nothing is translated."""

    id = "rule-only"
    uses_llm = False
    translates = False

    def translate(self, clause: ClauseInput) -> str | None:
        return None

    def confirm(self, clause: ClauseInput, candidates: Candidates, feedback: str | None) -> Judgement:
        return rule_judgement(candidates)


class ScriptedProvider:
    """Test double: returns queued judgements per clause and records every call."""

    uses_llm = True

    def __init__(
        self,
        script: dict[str, list[Judgement | Exception]] | None = None,
        *,
        translation: str | None = None,
        provider_id: str = "scripted",
    ) -> None:
        self.id = provider_id
        self.translates = translation is not None
        self._script = {key: list(value) for key, value in (script or {}).items()}
        self._translation = translation
        self._lock = threading.Lock()
        self.confirm_calls: list[tuple[str, str | None]] = []

    def translate(self, clause: ClauseInput) -> str | None:
        return self._translation

    def confirm(self, clause: ClauseInput, candidates: Candidates, feedback: str | None) -> Judgement:
        with self._lock:
            self.confirm_calls.append((clause.clause_id, feedback))
            queue = self._script.get(clause.clause_id)
            item = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else rule_judgement(candidates)
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
    """Judgements keyed by content hash. Values hold sentence numbers, never document text."""

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
