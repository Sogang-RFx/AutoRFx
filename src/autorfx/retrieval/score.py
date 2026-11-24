"""Automatic scoring of the retrieval comparison against a question answer key (issue #14).

``compare.py`` lists the top hits for a person to judge. With an answer key the
judging is done here: for every question and RFQ the key says which sentences
answer it, or that the RFQ cannot answer it.

Key format (YAML, kept under ``data/answer_keys/``, not committed: it describes
the development RFQs)::

    source: 채민 RFQ_조항별_분류표.xlsx (11/22)   # where the answers came from
    samples:
      S1_Nordwerk:            # matched against the sample name (substring)
        Q02:
          answers:
            - clause: d01:6.1                       # a parsed clause id, or
            - pattern: "Gewährleistungsfrist"       # a regex on the sentence text
            - pattern: "returned"                   #   (optionally limited to one file)
              file: d05
          ref: "01 RFQ 본문 6.1"                    # the answer-key row, for people
        Q10: none             # the RFQ cannot answer: refusing is correct
        Q08: skip             # not judged (ambiguous in the answer key)

A hit is correct when one answer matches it. An answer with both ``clause`` and
``pattern`` needs both (the sentence inside that clause); with one of them, that one.
Patterns exist because the parser splits some RFQs into very large clauses
(issue #5); there a clause id would mark half the document correct.

For each search mode and evidence gate the scorer counts, per question:

* answerable: ``hit`` (a correct sentence in the top k), ``wrong`` (answers, none
  correct) or ``refused`` (no evidence);
* not answerable: ``refused`` (correct) or ``answered`` (wrong).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from autorfx.guard import assert_not_held_out
from autorfx.models import Group, ParsedSentence
from autorfx.retrieval.compare import Question
from autorfx.retrieval.index import SearchIndex

NONE = "none"
SKIP = "skip"
MODES = ("bm25", "dense", "hybrid")
DEFAULT_GATES = (0.20, 0.30, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65)
"""Spans the measured gate of every model (3-large 0.30, 3-small 0.45, BGE-M3 0.55)."""


@dataclass(frozen=True, slots=True)
class Answer:
    clause: str | None = None
    pattern: re.Pattern[str] | None = None
    file: str | None = None

    def matches(self, sentence: ParsedSentence, clause_id: str) -> bool:
        if self.file and sentence.locus.source_doc != self.file:
            return False
        if self.clause and clause_id != self.clause:
            return False
        return self.pattern is None or bool(self.pattern.search(sentence.text))


@dataclass(frozen=True, slots=True)
class Expected:
    kind: str
    """"answer", "none" or "skip"."""
    answers: tuple[Answer, ...] = ()
    ref: str = ""

    def correct(self, sentence: ParsedSentence, clause_id: str) -> bool:
        return any(a.matches(sentence, clause_id) for a in self.answers)


@dataclass(frozen=True, slots=True)
class SampleKey:
    match: str
    questions: dict[str, Expected]


@dataclass(frozen=True, slots=True)
class AnswerKey:
    source: str
    samples: tuple[SampleKey, ...]

    def for_sample(self, name: str) -> SampleKey | None:
        return next((s for s in self.samples if s.match in name), None)


def _expected(raw: object) -> Expected:
    if raw in (NONE, SKIP):
        return Expected(str(raw))
    if not isinstance(raw, dict) or not isinstance(raw.get("answers"), list) or not raw["answers"]:
        raise ValueError(f"정답 항목은 'none', 'skip' 또는 answers 목록이어야 합니다: {raw!r}")
    answers = []
    for item in raw["answers"]:
        if not isinstance(item, dict) or not (item.get("clause") or item.get("pattern")):
            raise ValueError(f"answers 항목에 clause 또는 pattern이 필요합니다: {item!r}")
        pattern = None
        if item.get("pattern"):
            try:
                pattern = re.compile(item["pattern"], re.IGNORECASE)
            except re.error as exc:
                raise ValueError(
                    f"정답 표의 pattern 문법이 잘못됐습니다 ({exc}): {item['pattern']!r}"
                ) from exc
        answers.append(Answer(item.get("clause"), pattern, item.get("file")))
    return Expected("answer", tuple(answers), str(raw.get("ref", "")))


def load_key(path: Path, *, question_ids: Iterable[str] | None = None) -> AnswerKey:
    """Read an answer key. The held-out evaluation set is refused (INV-11).

    With ``question_ids`` an unknown id (``Q1`` for ``Q01``) is an error instead of being
    skipped without a word.
    """
    assert_not_held_out(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("samples") or {}, dict):
        raise ValueError(f"정답 표의 samples는 'RFQ 이름: 질문별 정답' 형태여야 합니다: {path}")
    for match, questions in raw["samples"].items() if raw.get("samples") else ():
        if questions is not None and not isinstance(questions, dict):
            raise ValueError(f"정답 표의 {match} 아래는 'Q01: ...' 형태여야 합니다: {questions!r}")
    samples = tuple(
        SampleKey(str(match), {str(q): _expected(v) for q, v in (questions or {}).items()})
        for match, questions in (raw.get("samples") or {}).items()
    )
    if not samples:
        raise ValueError(f"정답 표에 samples가 없습니다: {path}")
    if question_ids is not None:
        known = set(question_ids)
        unknown = sorted({f"{s.match} {q}" for s in samples for q in s.questions if q not in known})
        if unknown:
            raise ValueError(f"질문 목록에 없는 질문 ID가 정답 표에 있습니다: {', '.join(unknown)}")
    return AnswerKey(str(raw.get("source", "")), samples)


@dataclass(slots=True)
class Tally:
    answerable: int = 0
    hit: int = 0
    wrong: int = 0
    refused_wrongly: int = 0
    unanswerable: int = 0
    refused: int = 0
    answered_wrongly: int = 0
    misses: list[str] = field(default_factory=list)
    """``"S1 Q07: wrong"`` and the like, for the report."""

    def add(self, other: Tally) -> None:
        for name in (
            "answerable",
            "hit",
            "wrong",
            "refused_wrongly",
            "unanswerable",
            "refused",
            "answered_wrongly",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.misses.extend(other.misses)

    @property
    def judged(self) -> int:
        return self.answerable + self.unanswerable

    @property
    def correct(self) -> int:
        """Questions handled right: a correct sentence found, or a correct refusal."""
        return self.hit + self.refused


def _hits(index: SearchIndex, question: str, *, mode: str, group: Group, top: int, gate: float | None):
    """``(sentence, clause_id)`` of the hits a mode returns at a gate (the gate only affects dense)."""
    if mode == "bm25":
        result = index.search(question, group=group, limit=top, mode="bm25")
        return [(h.sentence, h.clause_id) for h in result.hits] if result.passed else []
    dense = index.dense
    if dense is None:
        raise ValueError("임베딩 모델이 설정되지 않았습니다.")
    saved = dense.min_score
    try:
        if gate is not None:
            dense.min_score = gate
        result = index.search(question, group=group, limit=top, mode=mode)
    finally:
        dense.min_score = saved
    return [(h.sentence, h.clause_id) for h in result.hits] if result.passed else []


def score_sample(
    index: SearchIndex,
    questions: Sequence[Question],
    key: SampleKey,
    *,
    label: str,
    mode: str,
    gate: float | None = None,
    top: int = 3,
    group: Group = "cost_visible",
) -> Tally:
    tally = Tally()
    for question in questions:
        expected = key.questions.get(question.id)
        if expected is None or expected.kind == SKIP:
            continue
        hits = _hits(index, question.text, mode=mode, group=group, top=top, gate=gate)
        if expected.kind == NONE:
            tally.unanswerable += 1
            if hits:
                tally.answered_wrongly += 1
                tally.misses.append(f"{label} {question.id}: 답이 없는데 답함")
            else:
                tally.refused += 1
            continue
        tally.answerable += 1
        if not hits:
            tally.refused_wrongly += 1
            tally.misses.append(f"{label} {question.id}: 답이 있는데 거절")
        elif any(expected.correct(s, c) for s, c in hits):
            tally.hit += 1
        else:
            tally.wrong += 1
            tally.misses.append(f"{label} {question.id}: 틀린 문장만")
    return tally


def gate_rows(
    runs: Iterable[tuple[str, SearchIndex, SampleKey]],
    questions: Sequence[Question],
    *,
    gates: Sequence[float] = DEFAULT_GATES,
    top: int = 3,
) -> list[tuple[str, float | None, Tally]]:
    """``(mode, gate, totals over all samples)``: BM25 once, dense and hybrid at every gate."""
    runs = list(runs)
    out: list[tuple[str, float | None, Tally]] = []
    plan: list[tuple[str, float | None]] = [("bm25", None)]
    plan += [(mode, gate) for mode in ("dense", "hybrid") for gate in gates]
    for mode, gate in plan:
        total = Tally()
        for label, index, key in runs:
            total.add(score_sample(index, questions, key, label=label, mode=mode, gate=gate, top=top))
        out.append((mode, gate, total))
    return out


LABEL = {"bm25": "BM25", "dense": "임베딩", "hybrid": "하이브리드"}


def to_markdown(
    rows: Sequence[tuple[str, float | None, Tally]],
    *,
    model: str,
    source: str,
    top: int,
    missing: Sequence[str] = (),
) -> str:
    first = rows[0][2]
    lines = [
        "# 검색 자동 채점 (정답 표 기준)",
        "",
        f"- 정답 출처: {source}",
        f"- 임베딩 모델: `{model}` · 질문별 상위 {top}개 · 판정한 질문×RFQ {first.judged}개 "
        f"(답 있음 {first.answerable}, 답 없음 {first.unanswerable})",
        "- **정답 찾음**: 상위 결과에 정답 문장이 하나라도 있음",
        "- **올바른 거절**: 답이 없는 질문에 '근거 없음'",
        *(
            [f"- ⚠ **일부만 채점**: 정답 표의 RFQ 중 {', '.join(missing)}이(가) 없어 빠졌습니다."]
            if missing
            else []
        ),
        "- ⚠ 원문은 넣지 않았지만 질문별 실패 목록은 RFQ 이름을 포함합니다. 깃허브에는 숫자 표만 올리세요.",
        "",
        "| 방식 | 기준 | 정답 찾음 | 틀린 문장만 | 잘못 거절 | 올바른 거절 | 답 없는데 답함 | 전체 정확 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for mode, gate, t in rows:
        g = "-" if gate is None else f"{gate:.2f}"
        lines.append(
            f"| {LABEL[mode]} | {g} | {t.hit}/{t.answerable} | {t.wrong} | {t.refused_wrongly} "
            f"| {t.refused}/{t.unanswerable} | {t.answered_wrongly} | **{t.correct}/{t.judged}** |"
        )
    hybrid = [r for r in rows if r[0] == "hybrid"]
    if not hybrid:
        return "\n".join(lines) + "\n"
    best = max(hybrid, key=lambda r: (r[2].correct, r[2].hit))
    lines += [
        "",
        f"하이브리드에서 전체 정확이 가장 높은 기준: **{best[1]:.2f}** ({best[2].correct}/{best[2].judged})",
        "",
        "## 실패 목록 (하이브리드, 위 기준)",
        "",
        *[f"- {m}" for m in best[2].misses],
        "",
    ]
    return "\n".join(lines)
