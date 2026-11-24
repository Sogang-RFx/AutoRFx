"""Why a clause has no translation: the status and note recorded per clause."""

from __future__ import annotations

import logging

import pytest

from autorfx.analysis import (
    RuleOnlyProvider,
    ScriptedProvider,
    TransientProviderError,
    analyze,
)
from autorfx.jobs import JobStore, load_job
from autorfx.models import ClauseAnalysis
from autorfx.rules import Ruleset
from autorfx.workspace import Workspace
from factories import make_clause

PLAIN = make_clause("1.1", ["1.1 Delivery is FCA supplier plant according to Incoterms 2020."])
BOILER = make_clause("1.2", ["1.2 All rights reserved."], is_boilerplate=True)
KOREAN = make_clause("1.3", ["1.3 납품은 공급사 공장 인도 조건이다."], lang="ko")
TIMEOUT = make_clause("1.4", ["1.4 Packaging is returnable."])
EXTRA = make_clause("1.5", ["1.5 Samples are due eight weeks before SOP."])
EMPTY = make_clause("1.6", ["1.6 Spare parts are delivered on request."])
CLAUSES = (PLAIN, BOILER, KOREAN, TIMEOUT, EXTRA)


def by_id(results: tuple[ClauseAnalysis, ...]) -> dict[str, ClauseAnalysis]:
    return {r.clause_id: r for r in results}


def test_each_missing_translation_has_a_reason(ruleset: Ruleset):
    """TRANS-03, INV-12: every clause without a translation says why, and a skipped one is not called."""
    plain = analyze([PLAIN], ruleset, RuleOnlyProvider())[0]
    assert (plain.translation_status, plain.translation_note) == ("no_provider", "")

    provider = ScriptedProvider(
        translations={
            "d01:1.1": ["인도조건은 FCA이다."],
            "d01:1.4": [TransientProviderError("TimeoutError")],
            "d01:1.5": ["샘플은 999개이다."],
            "d01:1.6": [""],
        }
    )
    results = by_id(analyze([PLAIN, BOILER, KOREAN, TIMEOUT, EXTRA, EMPTY], ruleset, provider))
    got = {k: (v.translation_status, v.translation_note) for k, v in results.items()}
    assert got == {
        "d01:1.1": ("translated", ""),
        "d01:1.2": ("boilerplate", ""),
        "d01:1.3": ("source_ko", ""),
        "d01:1.4": ("failed", "TimeoutError"),
        "d01:1.5": ("withheld", "extra_numbers=1"),
        "d01:1.6": ("failed", "empty"),
    }
    assert results["d01:1.5"].translation_ko is None
    assert sorted(provider.translate_calls) == ["d01:1.1", "d01:1.4", "d01:1.5", "d01:1.6"]


def test_failure_is_logged_with_the_clause_id_only(ruleset: Ruleset, caplog: pytest.LogCaptureFixture):
    """TRANS-03: a failed translation leaves a log line with the id and the exception name."""
    provider = ScriptedProvider(translations={"d01:1.4": [TransientProviderError("TimeoutError")]})
    with caplog.at_level(logging.WARNING):
        analyze([TIMEOUT], ruleset, provider)
    lines = [r.getMessage() for r in caplog.records if "d01:1.4" in r.getMessage()]
    assert lines and all("TimeoutError" in line and "returnable" not in line for line in lines)


def test_old_analysis_without_status_still_loads():
    """PIPE-VERSION: an analysis.json written before the fields existed loads as not recorded."""
    old = {
        "clause_id": "d01:1.1",
        "primary_department": "logistics",
        "priority": "should",
        "summary": {
            "finding": {"kind": "summary", "text": "", "citations": []},
            "citation_status": "verified",
        },
        "origin": "rule",
    }
    loaded = ClauseAnalysis.model_validate(old)
    assert (loaded.translation_status, loaded.translation_note) == ("not_recorded", "")


def test_translate_stage_counts_reasons(ruleset: Ruleset, workspace: Workspace):
    """UI-01: the stage detail counts clauses by reason and the counts add up to the clauses."""
    provider = ScriptedProvider(
        translations={
            "d01:1.1": ["인도조건은 FCA이다."],
            "d01:1.4": [TransientProviderError("TimeoutError")],
            "d01:1.5": ["샘플은 999개이다."],
        }
    )
    store = JobStore(workspace, started_at=1)
    store.start()
    analyze(CLAUSES, ruleset, provider, reporter=store)
    stages = {s.id: s for s in load_job(workspace).stages}  # type: ignore[union-attr]
    assert stages["translate"].status == "done"
    assert stages["translate"].detail == "번역 1 · 일부 0 · 보류 1 · 실패 1 · 제외 2"
