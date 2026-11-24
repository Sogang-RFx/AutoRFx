"""Side-by-side comparison of keyword (BM25), embedding and hybrid search on one RFQ.

For the team's retrieval check: a fixed list of questions is run through every
mode and the top hits are written as a Markdown table (to read) and a CSV with an
empty "관련(O/X)" column (to judge by hand in Excel). From the judged CSV,
precision@k per mode is the share of O.

The report quotes RFQ sentences, so it is written under ``data/work`` (not
committed). Share it in the team channel, never in the public repository.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from autorfx.models import Group, ParsedSentence
from autorfx.retrieval.index import SearchIndex

SNIPPET = 90
MODES = ("bm25", "dense", "hybrid")
LABEL = {"bm25": "BM25(현재)", "dense": "임베딩", "hybrid": "하이브리드(RRF)"}


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    text: str
    intent: str = ""
    """What the question is meant to test, e.g. "용어집에 없는 표현"."""


@dataclass(frozen=True, slots=True)
class Row:
    question: Question
    mode: str
    rank: int
    clause_id: str
    where: str
    text: str
    score: float | None
    """Cosine similarity for embedding hits; None for BM25 and hybrid ranks."""
    passed_gate: bool


def load_questions(path: Path) -> list[Question]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("questions", raw) if isinstance(raw, dict) else raw
    questions = []
    if not isinstance(items, list):
        raise ValueError(f"질문 파일은 questions 목록이어야 합니다: {path}")
    for number, item in enumerate(items, start=1):
        if isinstance(item, str):
            questions.append(Question(f"Q{number:02d}", item))
        elif not isinstance(item, dict) or not item.get("text"):
            raise ValueError(f"질문 {number}번에 text가 없습니다: {item!r} ({path})")
        else:
            questions.append(
                Question(str(item.get("id") or f"Q{number:02d}"), item["text"], item.get("intent", ""))
            )
    return questions


def _where(sentence: ParsedSentence) -> str:
    locus = sentence.locus
    return f"{locus.source_doc} p.{locus.page_number} s.{locus.sentence_index}"


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SNIPPET else text[: SNIPPET - 1] + "…"


def run(
    index: SearchIndex, questions: Sequence[Question], *, group: Group = "cost_visible", top: int = 3
) -> list[Row]:
    """Top ``top`` hits per question and mode. Embedding rows below the gate are kept and marked."""
    if index.dense is None:
        raise ValueError("임베딩 모델이 설정되지 않았습니다. .env에 EMBEDDING_MODEL을 넣어 주세요.")
    rows: list[Row] = []
    for question in questions:
        for mode in MODES:
            if mode == "dense":
                # Gate off, so the report also shows how close the best sentences came.
                found = index.dense.search(question.text, group=group, limit=top, min_score=-1.0)
                rows.extend(
                    Row(
                        question,
                        mode,
                        rank,
                        hit.clause_id,
                        _where(hit.sentence),
                        _snippet(hit.sentence.text),
                        round(hit.score, 3),
                        hit.score >= index.dense.min_score,
                    )
                    for rank, hit in enumerate(found, start=1)
                )
                continue
            result = index.search(question.text, group=group, limit=top, mode=mode)
            rows.extend(
                Row(
                    question,
                    mode,
                    h.rank,
                    h.clause_id,
                    _where(h.sentence),
                    _snippet(h.sentence.text),
                    None,
                    True,
                )
                for h in result.hits
            )
    return rows


def overlap(rows: Sequence[Row], question: Question, a: str = "bm25", b: str = "dense") -> str:
    """How many of the two modes' top hits are the same sentence, e.g. "1/3"."""
    first = {r.where for r in rows if r.question == question and r.mode == a and r.passed_gate}
    second = {r.where for r in rows if r.question == question and r.mode == b and r.passed_gate}
    if not first or not second:
        return "-"
    return f"{len(first & second)}/{max(len(first), len(second))}"


def to_markdown(
    rows: Sequence[Row], questions: Sequence[Question], *, title: str, model: str, gate: float
) -> str:
    lines = [
        f"# 검색 비교: {title}",
        "",
        f"- 임베딩 모델: `{model}` · 근거 기준(코사인) ≥ {gate}",
        "- BM25 열이 비어 있으면 키워드 검색이 '근거 없음'으로 답하지 않은 질문입니다.",
        "- 임베딩 열의 ✗ 표시는 기준 점수 미달(실제 답변에는 쓰이지 않음)입니다.",
        "- ⚠ 이 파일에는 RFQ 원문이 들어 있습니다. 깃허브에 올리지 마세요.",
        "",
        "## 요약",
        "",
        "| ID | 질문 | 의도 | BM25 결과 수 | 임베딩(기준 통과) | 겹침 | BM25 관련 | 임베딩 관련 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for q in questions:
        bm25 = sum(r.question == q and r.mode == "bm25" for r in rows)
        dense = [r for r in rows if r.question == q and r.mode == "dense"]
        best = f"{max((r.score or 0) for r in dense):.2f}" if dense else "-"
        passed = sum(r.passed_gate for r in dense)
        lines.append(
            f"| {q.id} | {q.text} | {q.intent} | {bm25 or '근거 없음'} | {passed} (최고 {best}) "
            f"| {overlap(rows, q)} |  /3 |  /3 |"
        )
    lines += ["", "## 질문별 상위 결과", ""]
    for q in questions:
        lines += [
            f"### {q.id}. {q.text}",
            "",
            "| 방식 | 순위 | 위치 | 조항 | 점수 | 문장 |",
            "|---|---|---|---|---|---|",
        ]
        for mode in MODES:
            mine = [r for r in rows if r.question == q and r.mode == mode]
            if not mine:
                lines.append(f"| {LABEL[mode]} | - | - | - | - | (근거 없음) |")
            for r in mine:
                score = "" if r.score is None else f"{r.score:.3f}{'' if r.passed_gate else ' ✗'}"
                text = r.text.replace("|", "\\|")
                lines.append(f"| {LABEL[mode]} | {r.rank} | {r.where} | {r.clause_id} | {score} | {text} |")
        lines.append("")
    return "\n".join(lines)


def to_csv(rows: Sequence[Row], path: Path) -> None:
    """One row per hit, with an empty column to mark O/X. UTF-8 with BOM so Excel reads Korean."""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "질문ID",
                "질문",
                "의도",
                "방식",
                "순위",
                "위치",
                "조항",
                "점수",
                "기준통과",
                "문장",
                "관련(O/X)",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.question.id,
                    r.question.text,
                    r.question.intent,
                    LABEL[r.mode],
                    r.rank,
                    r.where,
                    r.clause_id,
                    "" if r.score is None else r.score,
                    "Y" if r.passed_gate else "N",
                    r.text,
                    "",
                ]
            )
