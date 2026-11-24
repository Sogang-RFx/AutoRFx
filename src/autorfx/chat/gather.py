"""Gathering the evidence of an answer (RAG-03, RAG-04, INV-4, INV-7, docs/design/chat.md §3.3).

A small package is given whole. In a larger one sentences are found and their clauses are given:
no similarity threshold decides here, because the model judges the evidence and every statement it
writes is verified. Only text the group may see is searched, measured or returned.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import takewhile

from autorfx.access import Key
from autorfx.auth.policy import Scope
from autorfx.chat.models import ChatContext, Evidence
from autorfx.chat.stored import visible_issues
from autorfx.models import Clause, ParsedSentence
from autorfx.retrieval.index import RRF_K

log = logging.getLogger(__name__)

MAX_CHARS = 24_000
MAX_QUERIES = 6
SEARCH_LIMIT = 30
WHOLE_CLAUSE = 12
"""A clause of at most this many sentences is given whole."""
NEIGHBOURS = 3
NO_GATE = -1.0


def gather(
    question: str, queries: Sequence[str], *, context: ChatContext, scope: Scope, overview: bool
) -> tuple[Evidence, ...]:
    """The evidence for one question, in document order and numbered from 1."""
    visible = [c for c in context.clauses if scope.cost_visible or not c.is_cost_sensitive]
    if sum(_size(c.sentences) for c in visible) <= MAX_CHARS:
        chosen = frozenset(s.locus.key() for c in visible for s in c.sentences)
    else:
        scores = _scores((question, *queries[:MAX_QUERIES]), context, scope)
        chosen = _select(visible, scores, _leads(visible) if overview else ())
    return _numbered(visible, chosen | _other_sides(chosen, context, scope), context, scope)


# ── search ──────────────────────────────────────────────────────────────────
def _scores(texts: Iterable[str], context: ChatContext, scope: Scope) -> dict[Key, float]:
    """Reciprocal rank fusion over every query and both indexes."""
    scores: dict[Key, float] = defaultdict(float)
    for ranking in _rankings(tuple(dict.fromkeys(t.strip() for t in texts if t.strip())), context, scope):
        for rank, sentence in enumerate(ranking, start=1):
            scores[sentence.locus.key()] += 1 / (RRF_K + rank)
    return dict(scores)


def _rankings(texts: Sequence[str], context: ChatContext, scope: Scope) -> list[list[ParsedSentence]]:
    index = context.index
    lexical = [index.search_bm25(text, limit=SEARCH_LIMIT).hits for text in texts]
    return [[hit.sentence for hit in hits] for hits in lexical] + _dense_rankings(texts, context, scope)


def _dense_rankings(texts: Sequence[str], context: ChatContext, scope: Scope) -> list[list[ParsedSentence]]:
    dense = context.index.dense
    if dense is None:
        return []
    found: list[list[ParsedSentence]] = []
    for text in texts:
        try:
            hits = dense.search(text, group=scope.group, limit=SEARCH_LIMIT, min_score=NO_GATE)
        except Exception as exc:  # any encoder failure costs recall, not the answer (INV-12)
            log.warning("dense search failed (%s); keyword search only", type(exc).__name__)
            return []
        found.append([hit.sentence for hit in hits])
    return found


# ── selection ───────────────────────────────────────────────────────────────
def _select(
    visible: Sequence[Clause], scores: Mapping[Key, float], leads: Sequence[Clause]
) -> frozenset[Key]:
    chosen: dict[Key, int] = {}
    # Half of the budget at most, so that the best match still has room.
    share = MAX_CHARS // (2 * len(leads)) if leads else 0
    for clause in leads:
        chosen |= _sizes(_fitting(clause.sentences, share))
    for position, clause in enumerate(_ranked(visible, scores)):
        new = [s for s in _by_relevance(clause, scores) if s.locus.key() not in chosen]
        left = MAX_CHARS - sum(chosen.values())
        if _size(new) > left:
            # The most relevant part of it, and on to the next: a bulky clause must not leave the
            # budget unused. Only the best clause gets in whatever its size.
            new = _fitting(new, left, always_first=position == 0)
        chosen |= _sizes(new)
    return frozenset(chosen)


def _leads(visible: Sequence[Clause]) -> list[Clause]:
    """The first clause of each file and its front matter: what an overview starts from."""
    first = {c.source_doc: c.clause_id for c in reversed(visible)}
    return [c for c in visible if c.kind == "front_matter" or first[c.source_doc] == c.clause_id]


def _ranked(visible: Sequence[Clause], scores: Mapping[Key, float]) -> list[Clause]:
    best = [(max((scores.get(s.locus.key(), 0.0) for s in c.sentences), default=0.0), c) for c in visible]
    return [c for score, c in sorted(best, key=lambda item: -item[0]) if score > 0]


def _by_relevance(clause: Clause, scores: Mapping[Key, float]) -> list[ParsedSentence]:
    """The sentences to give for a matching clause, the matches first, so a cut keeps them."""
    sentences = clause.sentences
    hits = [i for i, s in enumerate(sentences) if s.locus.key() in scores]
    distance = [min(abs(i - hit) for hit in hits) for i in range(len(sentences))]
    headings = len(list(takewhile(lambda s: s.role == "heading", sentences)))
    whole = len(sentences) <= WHOLE_CLAUSE
    given = [i for i in range(len(sentences)) if whole or distance[i] <= NEIGHBOURS or i < headings]
    given.sort(key=lambda i: (distance[i], -scores.get(sentences[i].locus.key(), 0.0), i))
    return [sentences[i] for i in given]


def _fitting(
    sentences: Sequence[ParsedSentence], budget: int, *, always_first: bool = True
) -> list[ParsedSentence]:
    """As many as fit, in the given order. A sentence is never cut (INV-1)."""
    kept: list[ParsedSentence] = []
    for sentence in sentences:
        if (kept or not always_first) and _size(kept) + len(sentence.text) > budget:
            break
        kept.append(sentence)
    return kept


def _size(sentences: Iterable[ParsedSentence]) -> int:
    return sum(len(s.text) for s in sentences)


def _sizes(sentences: Iterable[ParsedSentence]) -> dict[Key, int]:
    return {s.locus.key(): len(s.text) for s in sentences}


# ── result ──────────────────────────────────────────────────────────────────
def _contradictions(context: ChatContext, scope: Scope) -> list[frozenset[Key]]:
    """The positions of each contradiction this scope may see (INV-8)."""
    visible = visible_issues(context, scope)
    return [frozenset(e.locus.key() for e in i.evidence) for i in visible if i.category == "conflict"]


def _other_sides(chosen: frozenset[Key], context: ChatContext, scope: Scope) -> frozenset[Key]:
    """Evidence of the contradictions the gathered sentences take part in, whatever the budget."""
    return frozenset(key for keys in _contradictions(context, scope) if keys & chosen for key in keys)


def _numbered(
    visible: Sequence[Clause], chosen: frozenset[Key], context: ChatContext, scope: Scope
) -> tuple[Evidence, ...]:
    """Only sentences of visible clauses can come out, whatever was chosen (INV-4)."""
    names = {f.id: f.name for f in context.files}
    given = [(c, s) for c in visible for s in c.sentences if s.locus.key() in chosen]
    titles = {c.clause_id: _title(c, context, scope) for c, _ in given}
    numbers = {s.locus.key(): number for number, (_, s) in enumerate(given, start=1)}
    contradictions = _contradictions(context, scope)

    def opposed(key: Key) -> tuple[int, ...]:
        others = {k for keys in contradictions if key in keys for k in keys if k != key}
        return tuple(sorted(numbers[k] for k in others if k in numbers))

    return tuple(
        Evidence(
            number=number,
            text=sentence.text,
            locus=sentence.locus,
            file=names.get(clause.source_doc, clause.source_doc),
            title=titles[clause.clause_id],
            contradicts=opposed(sentence.locus.key()),
        )
        for number, (clause, sentence) in enumerate(given, start=1)
    )


def _title(clause: Clause, context: ChatContext, scope: Scope) -> str:
    """Never an inherited title; and a sheet may be named after a price it does not state (INV-8)."""
    title = clause.own_title
    return "" if not scope.cost_visible and context.index.is_cost_question(title) else title
