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
from autorfx.analysis.translation import (
    WITHHELD,
    ClauseTranslation,
    SentenceTranslation,
    TranslationBatch,
    batches,
    clean,
    translate_clause,
)
from autorfx.jobs import JobStore, load_job
from autorfx.models import Clause, ClauseAnalysis
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
        "d01:1.6": ("failed", "missing=1"),
    }
    assert results["d01:1.5"].translation_ko is None
    asked = sorted(provider.translate_calls)
    assert asked == [
        ("d01:1.1", (1,)),
        ("d01:1.4", (1,)),
        ("d01:1.5", (1,)),
        ("d01:1.6", (1,)),
        ("d01:1.6", (1,)),
    ]


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


# ── sentence level (TRANS-01, CITE-02) ──────────────────────────────────────
def many(count: int, size: int = 10) -> Clause:
    letters = "abcdefghijklmnopqrstuvwxyz"
    return make_clause("9.1", [f"Sentence {letters[i % 26]} " + "x" * size for i in range(count)])


def run(clause: Clause, queue: list) -> tuple[ClauseTranslation, ScriptedProvider]:
    provider = ScriptedProvider(translations={clause.clause_id: queue})
    return translate_clause(clause, provider), provider


def test_batches_are_20_sentences_or_4000_chars_in_order():
    """TRANS-01: 45 sentences make three batches; one 5,000-character sentence is alone."""
    assert [len(b) for b in batches(["a"] * 45)] == [20, 20, 5]
    assert batches(["a" * 100, "b" * 5000, "c" * 100]) == [(1,), (2,), (3,)]
    grouped = batches(["a" * 1500] * 7)
    assert [n for b in grouped for n in b] == list(range(1, 8)) and max(len(b) for b in grouped) == 2


def test_only_the_sentence_with_an_added_number_is_withheld():
    """CITE-02: the number check compares a sentence with its own source sentence."""
    clause = make_clause("5.1", ["Pay in 30 days.", "Ship by train.", "Quote 5 items."])
    got, _ = run(clause, [{1: "30일 내 지급.", 2: "열차로 99대 발송.", 3: "5개 견적."}])
    assert got.status == "partial"
    assert got.sentences == ("30일 내 지급.", None, "5개 견적.")
    assert got.text == f"30일 내 지급. {WITHHELD} 5개 견적." and got.note == "extra_numbers=1"


def test_a_number_of_another_sentence_does_not_excuse_this_one():
    """CITE-02: sentence 2 states 30, which only sentence 1 has."""
    clause = make_clause("5.2", ["Pay in 30 days.", "Ship by train."])
    got, _ = run(clause, [{1: "30일 내 지급.", 2: "30일 내 발송."}])
    assert got.sentences == ("30일 내 지급.", None)


def test_status_of_a_clause_follows_its_sentences():
    """TRANS-03: all ok translated, mixed partial, all withheld withheld, otherwise failed."""
    clause = make_clause("5.3", ["Ship by train.", "Ship by road."])
    assert run(clause, [{1: "열차.", 2: "도로."}])[0].status == "translated"
    both = run(clause, [{1: "열차 7.", 2: "도로 8."}])[0]
    assert both.status == "withheld" and both.text is None and both.note == "extra_numbers=2"
    assert run(clause, [{1: "열차 7.", 2: "도로."}])[0].status == "partial"
    assert run(clause, [{}])[0].status == "failed"
    assert run(clause, [{1: "열차 7."}])[0].status == "failed", (
        "one withheld and one missing is not all withheld"
    )


def test_a_missing_number_is_asked_again_once():
    """TRANS-02: only the missing numbers are asked for, once."""
    clause = make_clause("5.4", ["Ship by train.", "Ship by road.", "Ship by air."])
    got, provider = run(clause, [{1: "열차.", 3: "항공."}, {2: "도로."}])
    assert got.status == "translated" and got.sentences == ("열차.", "도로.", "항공.")
    assert provider.translate_calls == [("d01:5.4", (1, 2, 3)), ("d01:5.4", (2,))]
    still, provider = run(clause, [{1: "열차.", 3: "항공."}, {}])
    assert still.sentences == ("열차.", None, "항공.") and still.status == "partial"
    assert still.note == "missing=1" and len(provider.translate_calls) == 2


def test_duplicate_and_out_of_range_numbers_are_dropped_in_both_replies():
    """TRANS-02: the first of a duplicate is kept; numbers that were not asked for are ignored."""
    clause = make_clause("5.5", ["Ship by train.", "Ship by road."])
    first = TranslationBatch(
        items=(
            SentenceTranslation(n=1, text="열차."),
            SentenceTranslation(n=1, text="다른 열차."),
            SentenceTranslation(n=7, text="없는 문장."),
        )
    )
    retry = TranslationBatch(
        items=(
            SentenceTranslation(n=2, text="도로."),
            SentenceTranslation(n=2, text="다른 도로."),
            SentenceTranslation(n=1, text="덮어쓰기."),
            SentenceTranslation(n=0, text="영."),
        )
    )
    got, _ = run(clause, [first, retry])
    assert got.sentences == ("열차.", "도로.") and got.status == "translated"


def test_a_blank_sentence_counts_as_missing():
    """TRANS-02: blank text is asked again and then failed."""
    clause = make_clause("5.6", ["Ship by train.", "Ship by road."])
    got, provider = run(clause, [{1: "열차.", 2: "  "}, {2: ""}])
    assert got.sentences == ("열차.", None) and got.note == "missing=1"
    assert len(provider.translate_calls) == 2


def test_exceptions_per_batch_keep_the_other_batches():
    """TRANS-03: one failed batch leaves partial, all failed leaves failed; the note has names only."""
    clause = many(25)
    ok = {n: "문장." for n in range(21, 26)}
    got, provider = run(clause, [TransientProviderError("TimeoutError"), ok])
    assert got.status == "partial" and got.note == "TimeoutError"
    assert got.sentences[:20] == (None,) * 20 and got.sentences[20] == "문장."
    assert len(provider.translate_calls) == 2, "a batch that raised is not asked again"
    dead, _ = run(clause, [TransientProviderError("TimeoutError")])
    assert dead.status == "failed" and dead.text is None and dead.note == "TimeoutError"


def test_an_exception_in_the_retry_keeps_the_rest():
    """TRANS-03: the answered sentences stay; the unanswered ones fail."""
    clause = make_clause("5.7", ["Ship by train.", "Ship by road."])
    got, _ = run(clause, [{1: "열차."}, TransientProviderError("RateLimitError")])
    assert got.sentences == ("열차.", None) and got.status == "partial" and got.note == "RateLimitError"


def test_note_orders_counts_before_exception_names():
    """TRANS-03: extra_numbers, missing, then distinct exception names, first seen first."""
    clause = many(25)
    first = {n: "문장." for n in range(1, 19)} | {20: "문장 9."}
    got, _ = run(clause, [first, {}, TransientProviderError("TimeoutError")])
    assert got.note == "extra_numbers=1; missing=1; TimeoutError"


def test_copied_markers_are_removed_unless_the_source_has_them():
    """CITE-02: "[S2] 포장은…" becomes "포장은…"; a marker of the source sentence stays."""
    clause = make_clause("5.8", ["Packaging is returnable.", "See [S2] of the annex."])
    got, _ = run(clause, [{1: "[S2] 포장은 반환용이다.", 2: "부속서 [S2] 참조."}])
    assert got.sentences == ("포장은 반환용이다.", "부속서 [S2] 참조.") and got.status == "translated"
    assert clean("A [S1] B", "plain") == "A B"
