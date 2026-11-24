"""Automatic retrieval scoring against an answer key (issue #14), with the shared test encoder."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autorfx.guard import GuardError
from autorfx.retrieval import SearchIndex, score
from autorfx.retrieval.compare import Question
from autorfx.retrieval.dense import DenseIndex
from autorfx.rules import Ruleset
from factories import make_sentence
from topic_encoder import CLAUSES, TopicEncoder

QUESTIONS = [
    Question("Q01", "견적서를 언제까지 제출해야 하나요?"),
    Question("Q02", "IATF certification"),
    Question("Q03", "대표이사 이름은?"),
    Question("Q04", "부품을 어떤 상자에 담아서 보내야 하나요?"),
]
IDS = [q.id for q in QUESTIONS]


def write_key(path: Path, questions: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
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
        ),
        question_ids=IDS,
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


def test_clause_and_pattern_together_need_both():
    """Review on #22: a pattern narrows a clause to one sentence instead of being ignored."""
    late = make_sentence("Late submissions are not considered.", sentence_index=2)
    due = make_sentence("Quotations must be received by 15 December 2026.", sentence_index=1)
    both = score.Answer(clause="d01:1.3", pattern=score.re.compile("received by"))
    assert both.matches(due, "d01:1.3")
    assert not both.matches(late, "d01:1.3")  # right clause, wrong sentence
    assert not both.matches(due, "d01:9.9")  # right sentence text, wrong clause
    assert score.Answer(clause="d01:1.3").matches(late, "d01:1.3")
    assert score.Answer(pattern=score.re.compile("Late")).matches(late, "d01:9.9")


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
    assert "하이브리드" in report and "전체 정확" in report and "일부만 채점" not in report
    partial = score.to_markdown(rows, model="test-topics", source="test", top=3, missing=["S1_Nordwerk"])
    assert "일부만 채점" in partial and "S1_Nordwerk" in partial


def test_report_without_hybrid_rows(index: SearchIndex, key: score.AnswerKey):
    rows = score.gate_rows([("demo", index, key.samples[0])], QUESTIONS, gates=())
    assert [m for m, _, _ in rows] == ["bm25"]
    assert "BM25" in score.to_markdown(rows, model="m", source="s", top=3)


@pytest.mark.parametrize(
    "bad",
    [
        {"Q01": {"answers": []}},
        {"Q01": {"answers": [{"file": "d01"}]}},
        {"Q01": "maybe"},
        {"Q01": {"answers": [{"pattern": "([unclosed"}]}},
        {"Q01": {"answers": ["d01:1.3"]}},
        {"Q01": {"answers": {"clause": "d01:1.3"}}},
        {"Q01": ["none"]},
    ],
)
def test_malformed_keys_are_refused(tmp_path: Path, bad: dict):
    with pytest.raises(ValueError):
        score.load_key(write_key(tmp_path / "bad.yaml", bad))


def test_samples_as_a_list_are_refused(tmp_path: Path):
    path = tmp_path / "list.yaml"
    path.write_text("samples:\n  - S1_Nordwerk\n", encoding="utf-8")
    with pytest.raises(ValueError, match="samples"):
        score.load_key(path)


def test_unknown_question_ids_are_refused(tmp_path: Path):
    """Review on #22: a typo like Q1 for Q01 must not be skipped without a word."""
    path = write_key(tmp_path / "typo.yaml", {"Q1": "none"})
    with pytest.raises(ValueError, match="Q1"):
        score.load_key(path, question_ids=IDS)
    assert score.load_key(path).samples  # without the question list there is nothing to check


def test_held_out_keys_are_refused(tmp_path: Path):
    """INV-11: the evaluation set is never read, answer keys included."""
    path = write_key(tmp_path / "eval" / "key.yaml", {"Q01": "none"})
    with pytest.raises(GuardError):
        score.load_key(path)


@pytest.mark.corpus
def test_the_local_answer_key_loads_if_present():
    path = Path(__file__).resolve().parents[2] / "data/answer_keys/retrieval_ko.yaml"
    if not path.is_file():
        pytest.skip("정답 표는 커밋하지 않습니다 (로컬 전용)")
    questions = Path(__file__).resolve().parents[2] / "data/questions/retrieval_ko.yaml"
    from autorfx.retrieval.compare import load_questions

    key = score.load_key(path, question_ids=[q.id for q in load_questions(questions)])
    assert {s.match for s in key.samples} >= {"S1_Nordwerk"}
