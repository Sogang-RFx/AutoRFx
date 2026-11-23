"""Automatic retrieval scoring against an answer key (issue #14), with the test encoder of test_dense."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from test_dense import CLAUSES, TopicEncoder

from autorfx.retrieval import SearchIndex, score
from autorfx.retrieval.compare import Question
from autorfx.retrieval.dense import DenseIndex
from autorfx.rules import Ruleset

QUESTIONS = [
    Question("Q01", "견적서를 언제까지 제출해야 하나요?"),
    Question("Q02", "IATF certification"),
    Question("Q03", "대표이사 이름은?"),
    Question("Q04", "부품을 어떤 상자에 담아서 보내야 하나요?"),
]


def write_key(path: Path, questions: dict) -> Path:
    path.write_text(yaml.safe_dump({"source": "test", "samples": {"demo": questions}}, allow_unicode=True))
    return path


@pytest.fixture()
def key(tmp_path: Path) -> score.AnswerKey:
    return score.load_key(
        write_key(
            tmp_path / "key.yaml",
            {
                "Q01": {"answers": [{"clause": "d01:1.3"}]},
                "Q02": {"answers": [{"pattern": "IATF"}]},
                "Q03": "none",
                "Q04": "skip",
            },
        )
    )


@pytest.fixture()
def index(ruleset: Ruleset) -> SearchIndex:
    return SearchIndex(CLAUSES, ruleset, dense=DenseIndex(CLAUSES, TopicEncoder(), min_score=0.5))


def test_key_matches_samples_by_substring(key: score.AnswerKey):
    assert key.for_sample("[SYN] demo package") is not None
    assert key.for_sample("other") is None


def test_bm25_misses_the_paraphrase_that_hybrid_finds(index: SearchIndex, key: score.AnswerKey):
    sample = key.samples[0]
    bm25 = score.score_sample(index, QUESTIONS, sample, label="demo", mode="bm25")
    hybrid = score.score_sample(index, QUESTIONS, sample, label="demo", mode="hybrid", gate=0.5)
    # Q04 is skipped: three questions judged, two answerable and one not.
    assert (bm25.answerable, bm25.unanswerable) == (2, 1)
    assert bm25.hit == 1 and bm25.refused_wrongly == 1 and "demo Q01: 답이 있는데 거절" in bm25.misses
    assert hybrid.hit == 2 and hybrid.refused == 1 and hybrid.correct == 3


def test_a_low_gate_answers_what_cannot_be_answered(index: SearchIndex, key: score.AnswerKey):
    sample = key.samples[0]
    loose = score.score_sample(index, QUESTIONS, sample, label="demo", mode="dense", gate=-1.0)
    assert loose.answered_wrongly == 1 and loose.refused == 0
    assert index.dense is not None and index.dense.min_score == 0.5  # the gate is restored


def test_gate_rows_and_report(index: SearchIndex, key: score.AnswerKey):
    rows = score.gate_rows([("demo", index, key.samples[0])], QUESTIONS, gates=(0.3, 0.5))
    assert [(m, g) for m, g, _ in rows] == [
        ("bm25", None),
        ("dense", 0.3),
        ("dense", 0.5),
        ("hybrid", 0.3),
        ("hybrid", 0.5),
    ]
    report = score.to_markdown(rows, model="test-topics", source="test", top=3)
    assert "하이브리드" in report and "전체 정확" in report


@pytest.mark.parametrize(
    "bad",
    [{"Q01": {"answers": []}}, {"Q01": {"answers": [{"file": "d01"}]}}, {"Q01": "maybe"}],
)
def test_malformed_keys_are_refused(tmp_path: Path, bad: dict):
    with pytest.raises(ValueError):
        score.load_key(write_key(tmp_path / "bad.yaml", bad))


def test_the_local_answer_key_loads_if_present():
    path = Path(__file__).resolve().parents[2] / "data/answer_keys/retrieval_ko.yaml"
    if not path.is_file():
        pytest.skip("정답 표는 커밋하지 않습니다 (로컬 전용)")
    key = score.load_key(path)
    assert {s.match for s in key.samples} >= {"S1_Nordwerk"}
