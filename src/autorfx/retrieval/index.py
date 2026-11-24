"""Lexical retrieval over sentences (RAG-01, RAG-02, INV-4).

No model is needed. Sentences are ranked with BM25 over concepts: each thing
the question asks about is one term, and all its wordings (the question's own
word, or every expansion of a glossary entry) count as that term. A sentence
is therefore not ranked higher for using three synonyms. A Korean question
finds English and German text through ``data/rules/glossary.yaml``.

Two indexes are built: one over every clause, one without the cost-sensitive
clauses. A restricted team's question is answered from the second one only,
so hidden text takes part in neither scoring nor answering (INV-4).

The evidence gate. A clause is evidence when it states

  (a) a domain term of the question (a glossary entry), or
  (b) two of the things asked about, or
  (c) one of them, when the question names no domain term and asks about
      nothing the text lacks.

A question that shares one ordinary word with the RFQ ("the CEO of the
supplier") gets "no evidence", not the nearest sentence. What was asked about
but is worded nowhere is reported back, so a partial match is not passed off
as a full one.
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from autorfx.chunker import mentions_cost
from autorfx.models import Clause, Group, ParsedSentence
from autorfx.retrieval.dense import DenseIndex
from autorfx.rules import GlossaryEntry, Ruleset

log = logging.getLogger(__name__)


WORD = re.compile(r"[^\W_]+(?:[.,][^\W_]+)*")
HANGUL = re.compile(r"[가-힣]")
K1, B = 1.5, 0.75
TITLE_WEIGHT = 1.0
MAX_SUGGESTIONS = 4
INFLECTIONS = ("s", "es", "e", "n", "en", "er")
RRF_K = 60
"""Reciprocal rank fusion constant (Cormack et al., 2009): a rank-r hit adds 1 / (RRF_K + r)."""
COMPOUND_MIN = 8
"""A word this long also matches a longer word that starts or ends with it (German compounds)."""
STOPWORDS = frozenset(
    """
    a an the is are was were be been do does did what which who whom whose when where why how many much
    can could should would will shall may must of in on at to for from by with about and or not no it its
    this that these those there here we you they i me my our your their he she his her them us tell show
    give please any some all per as if than then into such also have has had
    der die das den dem des ein eine einen einem einer eines ist sind war waren wird werden was welche
    welcher welches wer wie wo wann warum wieviel wieviele und oder nicht kein keine von im am an auf zu
    zum zur für mit bei über aus es sie er wir ihr dass ob auch gibt kann können muss müssen soll sollen
    bitte
    """.split()  # noqa: SIM905
)


def words(text: str) -> list[str]:
    """Case-folded word tokens; a number keeps its separators (``2.5``, ``1,20``)."""
    return [match.group().casefold() for match in WORD.finditer(text)]


def _same_stem(a: str, b: str) -> bool:
    """Equal, an inflection of each other, or a compound built on the other word."""
    shorter, longer = sorted((a, b), key=len)
    if shorter == longer:
        return True
    if longer.startswith(shorter):
        extra = longer[len(shorter) :]
        return (
            (len(shorter) >= 3 and extra in INFLECTIONS)
            or (len(shorter) >= 5 and len(extra) <= 3)
            or len(shorter) >= COMPOUND_MIN
        )
    return len(shorter) >= COMPOUND_MIN and longer.endswith(shorter)


@dataclass(frozen=True, slots=True)
class Concept:
    """One thing the question asks about, with every wording that counts as a match."""

    label: str
    alternatives: tuple[str, ...]
    is_domain_term: bool = False
    """From the glossary: a topic of the domain, however ordinary the word."""


@dataclass(frozen=True, slots=True)
class Hit:
    sentence: ParsedSentence
    clause_id: str
    rank: int


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: tuple[Hit, ...]
    concepts: tuple[str, ...]
    missing: tuple[str, ...]
    """Asked about, but worded nowhere in the text this group can see."""
    passed: bool


@dataclass(frozen=True, slots=True)
class _Text:
    tokens: tuple[str, ...]
    joined: str
    """The tokens separated and surrounded by spaces, for phrase matching."""

    @classmethod
    def of(cls, text: str) -> _Text:
        tokens = tuple(words(text))
        return cls(tokens, f" {' '.join(tokens)} ")

    def count(self, matching: frozenset[str], phrases: Sequence[str]) -> int:
        own = sum(token in matching for token in self.tokens)
        return own + sum(self.joined.count(phrase) for phrase in phrases)


@dataclass(frozen=True, slots=True)
class _Entry:
    sentence: ParsedSentence
    clause_id: str
    body: _Text
    title: _Text
    """The clause's own title. It says what the sentence is about without being part of it."""


class _Corpus:
    """The sentences of a set of clauses.

    A heading is not an answer, so it is indexed only as the title of the sentences below it;
    neither is a sentence without a letter in it (a stray list marker, a row of dashes).
    """

    def __init__(self, clauses: Sequence[Clause]) -> None:
        entries: list[_Entry] = []
        for clause in clauses:
            title = _Text.of(clause.own_title)
            body = [s for s in clause.sentences if s.role != "heading"] or list(clause.sentences)
            entries.extend(
                _Entry(sentence, clause.clause_id, _Text.of(sentence.text), title)
                for sentence in body
                if any(character.isalpha() for character in sentence.text)
            )
        self.entries = tuple(entries)
        self.vocabulary = frozenset(
            token for entry in entries for token in (*entry.body.tokens, *entry.title.tokens)
        )
        self.has_hangul = any(HANGUL.search(token) for token in self.vocabulary)
        self._average = sum(len(entry.body.tokens) for entry in entries) / len(entries) if entries else 0.0

    def occurrences(self, concept: Concept) -> dict[int, tuple[int, bool]]:
        """Per sentence that states the concept: how often it does itself, and whether its title does."""
        single = [a for a in concept.alternatives if " " not in a]
        phrases = [f" {a} " for a in concept.alternatives if " " in a]
        matching = frozenset(t for t in self.vocabulary if any(_same_stem(t, a) for a in single))
        found: dict[int, tuple[int, bool]] = {}
        for index, entry in enumerate(self.entries):
            own, titled = entry.body.count(matching, phrases), entry.title.count(matching, phrases) > 0
            if own or titled:
                found[index] = (own, titled)
        return found

    def score(self, index: int, own: int, titled: bool, stated_in: int) -> float:
        """BM25 of one concept for one sentence, plus a fixed share when the clause title states it.

        The title share is the same for every sentence of the clause: it lifts the clause without
        letting its shortest sentence win, and a sentence that states the concept itself comes first.
        """
        idf = math.log(1 + (len(self.entries) - stated_in + 0.5) / (stated_in + 0.5))
        # A long sentence is penalised; a short one is not rewarded. A form label ("15. DELIVER TO")
        # is not a better answer than a sentence of ordinary length.
        length = max(1.0, 1 - B + B * len(self.entries[index].body.tokens) / self._average)
        return idf * (own * (K1 + 1) / (own + K1 * length) + (TITLE_WEIGHT if titled else 0.0))


class SearchIndex:
    """Retrieval for one RFQ package, per access group.

    With a ``dense`` index the default search is hybrid: the BM25 hits that pass the evidence gate and
    the embedding hits that pass the similarity gate are merged by reciprocal rank fusion. Without one
    it is BM25 alone, exactly as before.
    """

    def __init__(
        self, clauses: Sequence[Clause], ruleset: Ruleset, *, dense: DenseIndex | None = None
    ) -> None:
        self._clauses = tuple(clauses)
        self._ruleset = ruleset
        self.dense = dense
        self._corpora: dict[Group, _Corpus] = {
            "cost_visible": _Corpus(clauses),
            "cost_restricted": _Corpus([c for c in clauses if not c.is_cost_sensitive]),
        }

    def scoped(self, clause_ids: AbstractSet[str]) -> ScopedSearchIndex:
        """An index of ``clause_ids`` alone: BM25 statistics, missing terms, suggestions and dense rows."""
        allowed = [c for c in self._clauses if c.clause_id in clause_ids]
        dense = self.dense.subset(clause_ids) if self.dense is not None else None
        return ScopedSearchIndex(allowed, self._ruleset, dense=dense)

    def _corpus(self, group: Group) -> _Corpus:
        # Anything that is not the cost-visible group reads the reduced index.
        return self._corpora["cost_visible" if group == "cost_visible" else "cost_restricted"]

    def concepts(self, question: str, *, keep_hangul: bool = True) -> tuple[Concept, ...]:
        """What the question asks about: glossary entries it mentions, then its remaining content words.

        The longest wording wins and uses up its words: "단가 인하" is the price reduction, not the
        price as well. A Korean term may carry a particle ("납기는"); a word may be inflected.
        """
        return _concepts_for(self._ruleset, question, keep_hangul=keep_hangul)

    def search(self, question: str, *, group: Group, limit: int = 8, mode: str | None = None) -> SearchResult:
        """``mode``: "bm25", "dense" or "hybrid"; by default hybrid when a dense index is attached."""
        return _search_with(
            question,
            corpus=self._corpus(group),
            ruleset=self._ruleset,
            dense=self.dense,
            dense_group=group,
            limit=limit,
            mode=mode,
        )

    def search_bm25(self, question: str, *, group: Group, limit: int = 8) -> SearchResult:
        return _search_bm25(question, corpus=self._corpus(group), ruleset=self._ruleset, limit=limit)

    def is_cost_question(self, question: str) -> bool:
        """Decided from the question and the rule files alone; no document text is consulted."""
        wordings = [a for concept in self.concepts(question) for a in concept.alternatives]
        return mentions_cost(" ".join([question, *wordings]), self._ruleset)

    def suggestions(self, group: Group) -> list[str]:
        """Topics this group can ask about: glossary entries the visible text actually covers."""
        return _suggestions_for(self._corpus(group), self._ruleset)


class ScopedSearchIndex:
    """Retrieval over the clauses visible to one account (INV-4, docs/auth-rbac.md §4).

    Built by :meth:`SearchIndex.scoped`: every BM25 statistic, missing-term list, topic suggestion
    and embedding row comes exclusively from those clauses.
    """

    def __init__(
        self, clauses: Sequence[Clause], ruleset: Ruleset, *, dense: DenseIndex | None = None
    ) -> None:
        self._clauses = tuple(clauses)
        self._ruleset = ruleset
        self.dense = dense
        self._corpus = _Corpus(clauses)

    def scoped(self, clause_ids: AbstractSet[str]) -> ScopedSearchIndex:
        """Further narrow this index; never widens past the clauses already in it."""
        allowed = [c for c in self._clauses if c.clause_id in clause_ids]
        dense = self.dense.subset(clause_ids) if self.dense is not None else None
        return ScopedSearchIndex(allowed, self._ruleset, dense=dense)

    def concepts(self, question: str, *, keep_hangul: bool = True) -> tuple[Concept, ...]:
        return _concepts_for(self._ruleset, question, keep_hangul=keep_hangul)

    def search(self, question: str, *, limit: int = 8, mode: str | None = None) -> SearchResult:
        # ``self.dense`` is already subsetted to the account's visible clauses (which already excludes
        # cost-sensitive clauses when the account cannot see costs, and keeps them for cost-visible scopes).
        return _search_with(
            question,
            corpus=self._corpus,
            ruleset=self._ruleset,
            dense=self.dense,
            dense_group="cost_visible",
            limit=limit,
            mode=mode,
        )

    def search_bm25(self, question: str, *, limit: int = 8) -> SearchResult:
        return _search_bm25(question, corpus=self._corpus, ruleset=self._ruleset, limit=limit)

    def is_cost_question(self, question: str) -> bool:
        wordings = [a for concept in self.concepts(question) for a in concept.alternatives]
        return mentions_cost(" ".join([question, *wordings]), self._ruleset)

    def suggestions(self) -> list[str]:
        return _suggestions_for(self._corpus, self._ruleset)


def _concepts_for(ruleset: Ruleset, question: str, *, keep_hangul: bool = True) -> tuple[Concept, ...]:
    glossary = ruleset.glossary
    text, tokens = question.casefold(), words(question)
    candidates = sorted(
        ((wording, number) for number, entry in enumerate(glossary) for wording in _wordings(entry)),
        key=lambda candidate: (-len(candidate[0]), candidate[1]),
    )
    labels: dict[int, str] = {}
    for wording, number in candidates:
        if HANGUL.search(wording):
            typed, text = (wording if wording in text else None), text.replace(wording, " ")
        else:
            tokens, typed = _without(tokens, words(wording))
        if typed:
            labels.setdefault(number, typed)

    found: list[Concept] = []
    claimed: set[str] = set()
    korean: list[str] = []
    for number in sorted(labels):
        alternatives = _alternatives(glossary[number])
        found.append(Concept(labels[number], alternatives, is_domain_term=True))
        claimed.update(token for alternative in alternatives for token in alternative.split())
        korean.extend(w for w in _wordings(glossary[number]) if HANGUL.search(w))
    for token in dict.fromkeys(token for token in tokens if token):
        if token in STOPWORDS or any(_same_stem(token, word) for word in claimed):
            continue
        part_of_term = any(term in token or token in term for term in korean)
        if HANGUL.search(token) and (not keep_hangul or part_of_term):
            continue
        found.append(Concept(token, (token,)))
    return tuple(found)


def _search_with(
    question: str,
    *,
    corpus: _Corpus,
    ruleset: Ruleset,
    dense: DenseIndex | None,
    dense_group: Group,
    limit: int,
    mode: str | None,
) -> SearchResult:
    explicit = mode is not None
    mode = mode or ("hybrid" if dense is not None else "bm25")
    if mode == "bm25":
        return _search_bm25(question, corpus=corpus, ruleset=ruleset, limit=limit)
    if dense is None:
        raise ValueError("임베딩 모델이 설정되지 않았습니다 (EMBEDDING_MODEL).")
    try:
        dense_hits = dense.search(question, group=dense_group, limit=limit)
    except Exception:
        if explicit:
            raise
        # The question could not be embedded (network, key, rate limit): keyword search answers.
        log.exception("embedding search failed, answering with keyword search only")
        return _search_bm25(question, corpus=corpus, ruleset=ruleset, limit=limit)
    if mode == "dense":
        hits = tuple(Hit(h.sentence, h.clause_id, rank) for rank, h in enumerate(dense_hits, start=1))
        return SearchResult(hits, (), (), bool(hits))
    if mode != "hybrid":
        raise ValueError(f"알 수 없는 검색 방식입니다: {mode}")
    lexical = _search_bm25(question, corpus=corpus, ruleset=ruleset, limit=limit)
    fused: dict[tuple, float] = defaultdict(float)
    first: dict[tuple, tuple[ParsedSentence, str]] = {}
    for ranked in (
        [(h.sentence, h.clause_id) for h in lexical.hits],
        [(h.sentence, h.clause_id) for h in dense_hits],
    ):
        for rank, (sentence, clause_id) in enumerate(ranked, start=1):
            key = sentence.locus.key()
            fused[key] += 1 / (RRF_K + rank)
            first.setdefault(key, (sentence, clause_id))
    order = sorted(fused, key=lambda key: -fused[key])[:limit]
    hits = tuple(Hit(*first[key], rank) for rank, key in enumerate(order, start=1))
    return SearchResult(hits, lexical.concepts, lexical.missing, bool(hits))


def _search_bm25(question: str, *, corpus: _Corpus, ruleset: Ruleset, limit: int = 8) -> SearchResult:
    concepts = _concepts_for(ruleset, question, keep_hangul=corpus.has_hangul)
    labels = tuple(concept.label for concept in concepts)
    found = [corpus.occurrences(concept) for concept in concepts]
    missing = tuple(c.label for c, stated in zip(concepts, found, strict=True) if not stated)

    by_clause: dict[str, set[int]] = defaultdict(set)
    for number, stated in enumerate(found):
        for index in stated:
            by_clause[corpus.entries[index].clause_id].add(number)
    domain = {n for n, concept in enumerate(concepts) if concept.is_domain_term and found[n]}
    # (c) is for a question that names no domain term and asks about nothing the text lacks.
    enough = 1 if not domain and not missing else 2
    evidence = {
        clause_id for clause_id, numbers in by_clause.items() if numbers & domain or len(numbers) >= enough
    }

    scores: dict[int, float] = defaultdict(float)
    for stated in found:
        for index, (own, titled) in stated.items():
            if corpus.entries[index].clause_id in evidence:
                scores[index] += corpus.score(index, own, titled, len(stated))
    order = sorted(scores, key=lambda index: (-scores[index], index))
    hits = tuple(
        Hit(corpus.entries[index].sentence, corpus.entries[index].clause_id, rank)
        for rank, index in enumerate(order[:limit], start=1)
    )
    return SearchResult(hits, labels, missing, bool(hits))


def _suggestions_for(corpus: _Corpus, ruleset: Ruleset) -> list[str]:
    out: list[str] = []
    for entry in ruleset.glossary:
        if corpus.occurrences(Concept(entry.terms[0], _alternatives(entry))):
            out.append(entry.terms[0])
        if len(out) == MAX_SUGGESTIONS:
            break
    return out


def _wordings(entry: GlossaryEntry) -> list[str]:
    """Every wording of a glossary entry, except those that are also a stop word.

    English "die" (a press tool) is the German article; counted as a wording it would match every
    German sentence.
    """
    folded = (wording.casefold() for wording in (*entry.terms, *entry.expands_to))
    return [wording for wording in folded if wording not in STOPWORDS]


def _alternatives(entry: GlossaryEntry) -> tuple[str, ...]:
    """The wordings that can occur in the documents, tokenised the way the documents are."""
    return tuple(dict.fromkeys(" ".join(words(w)) for w in _wordings(entry) if not HANGUL.search(w)))


def _without(tokens: Sequence[str], parts: Sequence[str]) -> tuple[list[str], str | None]:
    """``tokens`` with every occurrence of a wording blanked out, and the first one as it was typed.

    A single word may be inflected; a phrase must be whole.
    """
    remaining, width = list(tokens), len(parts)
    typed: str | None = None
    for start in range(len(tokens) - width + 1 if width else 0):
        window = remaining[start : start + width]
        if _same_stem(window[0], parts[0]) if width == 1 else window == list(parts):
            typed = typed or " ".join(window)
            remaining[start : start + width] = [""] * width
    return remaining, typed
