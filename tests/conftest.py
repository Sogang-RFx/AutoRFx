"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from autorfx.rules import Ruleset, load_ruleset
from autorfx.workspace import Workspace

REPO = Path(__file__).resolve().parents[1]
RULES_DIR = REPO / "data" / "rules"
CONTRACT_DIR = REPO / "contract"
DEV_CORPUS = REPO / "data" / "raw" / "dev"


@pytest.fixture(scope="session")
def ruleset() -> Ruleset:
    """The committed rule files: tests run against what the domain team edits."""
    return load_ruleset(RULES_DIR)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace.for_rfq(tmp_path / "work", "rfq-test000001").ensure()


@pytest.fixture(scope="session")
def dev_corpus() -> Path:
    """Local development RFQs. Tests that need them are skipped when they are absent."""
    if not DEV_CORPUS.is_dir():
        pytest.skip("data/raw/dev가 없습니다 (개발용 RFQ는 저장소에 없음)")
    return DEV_CORPUS
