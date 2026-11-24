"""The command line: list samples and analyse a package without the server."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from autorfx.analysis import ScriptedProvider
from autorfx.analysis.usage import Usage, usd_text
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


def test_score_search_leaves_stored_results_alone(data_dir: Path, score_files, fake_encoder):
    """A rule-only scoring run never replaces the served analysis of the same files (review on #22)."""
    questions, _ = score_files
    assert main(["analyze", "--sample", "synthetic-sample"]) == 0
    stored = {p: p.read_bytes() for p in (data_dir / "work").glob("rfq-*/*.json")}
    assert stored
    assert main(["score-search", "--model", "fake", "--questions", str(questions), "--gates", "0.5"]) == 0
    assert {p: p.read_bytes() for p in (data_dir / "work").glob("rfq-*/*.json")} == stored


def test_score_search_refuses_two_names_for_one_sample(
    data_dir: Path, score_files, fake_encoder, capsys: pytest.CaptureFixture[str]
):
    """Review on #22: 'synthetic-sample' and 'synthetic' both match one RFQ; say so plainly."""
    questions, key = score_files
    key.write_text(key.read_text(encoding="utf-8") + "  synthetic:\n    Q02: none\n", encoding="utf-8")
    assert main(["score-search", "--model", "fake", "--questions", str(questions)]) == 2
    err = capsys.readouterr().err
    assert "같은 RFQ" in err and "data/raw/dev에 없습니다" not in err


@pytest.mark.parametrize(
    "questions_yaml",
    ["questions:\n  - {id: Q01, intent: x}\n", "questions: {Q01: text}\n"],
)
def test_score_search_explains_a_malformed_question_file(
    data_dir: Path, score_files, fake_encoder, capsys: pytest.CaptureFixture[str], questions_yaml: str
):
    questions, _ = score_files
    questions.write_text(questions_yaml, encoding="utf-8")
    assert main(["score-search", "--model", "fake", "--questions", str(questions)]) == 2
    assert "질문" in capsys.readouterr().err


def test_score_search_explains_what_is_missing(
    data_dir: Path,
    score_files,
    fake_encoder,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    questions, key = score_files
    base = ["score-search", "--model", "fake", "--questions", str(questions)]
    assert main([*base, "--key", str(data_dir / "nope.yaml")]) == 2
    assert "정답 표가 없습니다" in capsys.readouterr().err
    assert main([*base, "--gates", "0.5,abc"]) == 2
    assert "--gates" in capsys.readouterr().err
    monkeypatch.setenv("EMBEDDING_MODEL", "")  # embedding search switched off on purpose
    get_settings.cache_clear()
    assert main(["score-search", "--questions", str(questions)]) == 2  # no model configured
    assert "임베딩 모델" in capsys.readouterr().err
    monkeypatch.delenv("EMBEDDING_MODEL")
    get_settings.cache_clear()

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


# ── estimated translation cost (INV-12) ─────────────────────────────────────
PRICE_FILE = (
    'source: https://example.test/pricing\nchecked: "2026-11-24"\nunit: per 1M\nmodels:\n'
    '  gpt-x:\n    input: "0.10"\n    cached_input: "0.01"\n    output: "0.50"\n'
)


@pytest.fixture
def translating(data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """A key is set and the provider is a script that translates; nothing leaves the machine."""
    import autorfx.cli as cli

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    get_settings.cache_clear()

    def build(model: str):
        provider = ScriptedProvider(
            translation="번역문",
            provider_id=f"openai:{model}",
            usage=Usage(calls=1, input_tokens=1000, output_tokens=1000),
        )
        monkeypatch.setattr(cli, "make_provider", lambda *a, **k: provider)

    return build


def test_analyze_prints_the_estimated_translation_cost(
    data_dir: Path, translating, capsys: pytest.CaptureFixture[str]
):
    """INV-12: the summary says "estimated" and gives the cost with the number of calls."""
    translating("gpt-x")
    (data_dir / "translation").mkdir()
    (data_dir / "translation" / "prices.yaml").write_text(PRICE_FILE, encoding="utf-8")
    assert main(["analyze", "--sample", "synthetic-sample"]) == 0
    line = next(x for x in capsys.readouterr().out.splitlines() if x.startswith("번역 추정 비용"))
    calls = int(line.split("(")[1].split("회")[0])
    cost = Decimal(calls) * Decimal("0.0006")
    assert line == f"번역 추정 비용: ${usd_text(cost)} ({calls}회 호출)"


def test_analyze_says_when_the_model_has_no_price(
    data_dir: Path, translating, capsys: pytest.CaptureFixture[str]
):
    """INV-12: an unpriced model is reported as such, not as zero."""
    translating("gpt-unpriced")
    (data_dir / "translation").mkdir()
    (data_dir / "translation" / "prices.yaml").write_text(PRICE_FILE, encoding="utf-8")
    assert main(["analyze", "--sample", "synthetic-sample"]) == 0
    assert "번역 추정 비용: 단가 없음 (" in capsys.readouterr().out


def test_analyze_with_a_key_needs_the_price_file(
    data_dir: Path, translating, capsys: pytest.CaptureFixture[str]
):
    """INV-12: a missing price file stops the run at the start, with a message naming it."""
    translating("gpt-x")
    assert main(["analyze", "--sample", "synthetic-sample"]) == 1
    assert "prices.yaml" in capsys.readouterr().err
    assert not list((data_dir / "work").glob("rfq-*"))


def test_analyze_without_a_key_prints_no_cost_and_needs_no_price_file(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
):
    """TRANS-03: without a key nothing is translated and the price file is not read."""
    assert main(["analyze", "--sample", "synthetic-sample"]) == 0
    assert "번역 추정 비용" not in capsys.readouterr().out


@pytest.fixture
def compare_questions(data_dir: Path) -> Path:
    path = data_dir / "questions.yaml"
    path.write_text('questions:\n  - {id: Q01, text: "보증 기간은 얼마인가요?"}\n', encoding="utf-8")
    return path


def test_compare_search_leaves_stored_results_alone(data_dir: Path, compare_questions: Path, fake_encoder):
    """Like score-search: a comparison run never replaces the served analysis of the same files."""
    assert main(["analyze", "--sample", "synthetic-sample"]) == 0
    stored = {p: p.read_bytes() for p in (data_dir / "work").glob("rfq-*/*.json")}
    assert stored
    argv = ["compare-search", "--sample", "synthetic-sample", "--model", "fake"]
    assert main([*argv, "--questions", str(compare_questions)]) == 0
    assert {p: p.read_bytes() for p in (data_dir / "work").glob("rfq-*/*.json")} == stored
    assert list((data_dir / "work" / "_compare").glob("*.md"))


def test_compare_search_never_builds_an_llm_provider(
    data_dir: Path,
    compare_questions: Path,
    fake_encoder,
    monkeypatch: pytest.MonkeyPatch,
):
    """Comparing searches spends no API calls, even with a key set."""
    import autorfx.cli as cli

    def fail(*args, **kwargs):
        raise AssertionError("compare-search must not build an LLM provider")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    get_settings.cache_clear()
    monkeypatch.setattr(cli, "make_provider", fail)
    argv = ["compare-search", "--sample", "synthetic-sample", "--model", "fake"]
    assert main([*argv, "--questions", str(compare_questions)]) == 0


def test_compare_search_checks_the_questions_before_any_work(
    data_dir: Path,
    compare_questions: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """A missing, malformed or held-out (INV-11) questions file stops the run before parsing and embedding."""
    import autorfx.cli as cli

    monkeypatch.setattr(cli, "run_pipeline", lambda *a, **k: pytest.fail("the pipeline ran"))
    monkeypatch.setattr(cli, "load_encoder", lambda *a, **k: pytest.fail("the model was loaded"))
    argv = ["compare-search", "--sample", "synthetic-sample", "--model", "fake", "--questions"]
    assert main([*argv, str(data_dir / "missing.yaml")]) == 2
    malformed = data_dir / "bad.yaml"
    malformed.write_text("questions: [", encoding="utf-8")
    assert main([*argv, str(malformed)]) == 2
    held_out = data_dir / "eval" / "questions.yaml"
    held_out.parent.mkdir()
    held_out.write_text(compare_questions.read_text(encoding="utf-8"), encoding="utf-8")
    assert main([*argv, str(held_out)]) == 2
    assert capsys.readouterr().err.strip()


def test_compare_search_uses_the_gate_measured_for_the_model(
    data_dir: Path, score_files, fake_encoder, capsys: pytest.CaptureFixture[str]
):
    """Without --min-score the gate is the model's own (3-large 0.30, BGE-M3 0.55), not one for all."""
    questions, _ = score_files
    base = ["compare-search", "--sample", "synthetic-sample", "--questions", str(questions)]
    for model, gate in (("openai:text-embedding-3-large", "0.3"), ("BAAI/bge-m3", "0.55")):
        assert main([*base, "--model", model]) == 0
        report = next((data_dir / "work" / "_compare").glob("*.md"))
        assert f"{gate}" in report.read_text(encoding="utf-8")
        report.unlink()
    assert main([*base, "--min-score", "1.5"]) != 0
    assert "-1에서 1 사이" in capsys.readouterr().err


def test_a_bad_min_score_is_refused_before_any_work(data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    import autorfx.cli as cli

    monkeypatch.setattr(cli, "run_pipeline", lambda *a, **k: pytest.fail("the pipeline ran"))
    monkeypatch.setattr(cli, "load_encoder", lambda *a, **k: pytest.fail("the model was loaded"))
    assert main(["compare-search", "--sample", "synthetic-sample", "--model", "x", "--min-score", "7"]) == 2
    assert "-1에서 1 사이" in capsys.readouterr().err
