"""The command line: list samples and analyse a package without the server."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from autorfx.cli import main
from autorfx.config import get_settings
from conftest import RULES_DIR
from factories import make_package


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A data folder with the committed rules and one hand-made sample; no key is configured."""
    root = tmp_path / "data"
    shutil.copytree(RULES_DIR, root / "rules")
    make_package(root / "raw" / "dev" / "synthetic-sample")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTORFX_DATA_DIR", str(root))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def test_samples_are_listed(data_dir: Path, capsys: pytest.CaptureFixture[str]):
    assert main(["samples"]) == 0
    assert capsys.readouterr().out == "synthetic-sample\t파일 2개\tsynthetic-sample\n"


def test_analyze_a_sample_prints_what_was_found_and_how(data_dir: Path, capsys: pytest.CaptureFixture[str]):
    """ENGINE-03: the summary names the engine, so nobody takes rule output for LLM output."""
    assert main(["analyze", "--sample", "synthetic-sample"]) == 0
    out = capsys.readouterr().out
    assert "파일 2개, 2쪽" in out and "조항 7개, 독소조항 후보 0개, 정합성 이슈 1건" in out
    assert "분석 엔진: rule-only (LLM 미연결)" in out
    assert len(list((data_dir / "work").glob("rfq-*/analysis.json"))) == 1


def test_analyze_files_given_by_path(data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    sources = make_package(tmp_path / "elsewhere")
    assert main(["analyze", *(str(p) for p in sources)]) == 0
    assert "조항 7개" in capsys.readouterr().out


def test_problems_are_reported_with_an_exit_code(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    assert main(["analyze", "--sample", "nope"]) == 2
    assert "nope" in capsys.readouterr().err
    assert main(["analyze"]) == 2
    note = tmp_path / "note.txt"
    note.write_text("not an rfq", encoding="utf-8")
    assert main(["analyze", str(note)]) == 1
    assert "지원하지 않는 형식" in capsys.readouterr().err


# --- score-search (#14) ------------------------------------------------------------------------

SCORE_QUESTIONS = 'questions:\n  - {id: Q01, text: "보증 기간은?"}\n  - {id: Q02, text: "대표이사 이름은?"}\n'


@pytest.fixture
def score_files(data_dir: Path) -> tuple[Path, Path]:
    """Questions and an answer key for the hand-made sample (warranty answered, the CEO not)."""
    questions = data_dir / "questions.yaml"
    questions.write_text(SCORE_QUESTIONS, encoding="utf-8")
    key = data_dir / "answer_keys" / "retrieval_ko.yaml"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(
        "source: test\n"
        "samples:\n"
        "  synthetic-sample:\n"
        "    Q01: {answers: [{pattern: warranty}]}\n"
        "    Q02: none\n",
        encoding="utf-8",
    )
    return questions, key


@pytest.fixture
def fake_encoder(monkeypatch: pytest.MonkeyPatch):
    import autorfx.cli as cli
    from topic_encoder import TopicEncoder

    monkeypatch.setattr(cli, "load_encoder", lambda *a, **k: TopicEncoder())


def test_score_search_runs_on_rules_only(
    score_files, fake_encoder, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """Scoring never builds an LLM provider, even with a key set (no API calls just to score)."""
    import autorfx.cli as cli

    questions, _ = score_files
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    get_settings.cache_clear()
    monkeypatch.setattr(cli, "make_provider", lambda *a, **k: pytest.fail("score-search built a provider"))
    assert main(["score-search", "--model", "fake", "--questions", str(questions), "--gates", "0.5"]) == 0
    out = capsys.readouterr().out
    assert "채점 대상: synthetic-sample" in out and "| BM25 | - | 1/1 |" in out
    assert "일부만 채점" not in out


def test_score_search_explains_what_is_missing(
    data_dir: Path, score_files, fake_encoder, capsys: pytest.CaptureFixture[str]
):
    questions, key = score_files
    base = ["score-search", "--model", "fake", "--questions", str(questions)]
    assert main([*base, "--key", str(data_dir / "nope.yaml")]) == 2
    assert "정답 표가 없습니다" in capsys.readouterr().err
    assert main([*base, "--gates", "0.5,abc"]) == 2
    assert "--gates" in capsys.readouterr().err
    assert main(["score-search", "--questions", str(questions)]) == 2  # no model configured
    assert "임베딩 모델" in capsys.readouterr().err

    key.write_text(key.read_text(encoding="utf-8") + "  other-rfq:\n    Q01: none\n", encoding="utf-8")
    assert main(base) == 2
    assert "other-rfq" in capsys.readouterr().err
    assert main([*base, "--allow-missing"]) == 0
    assert "일부만 채점" in capsys.readouterr().out


def test_score_search_refuses_the_evaluation_set(
    data_dir: Path, score_files, fake_encoder, capsys: pytest.CaptureFixture[str]
):
    questions, key = score_files
    held_out = data_dir / "eval" / "key.yaml"
    held_out.parent.mkdir()
    held_out.write_text(key.read_text(encoding="utf-8"), encoding="utf-8")
    assert (
        main(["score-search", "--model", "fake", "--questions", str(questions), "--key", str(held_out)]) == 2
    )
    assert "평가용(eval)" in capsys.readouterr().err
