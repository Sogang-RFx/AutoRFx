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
