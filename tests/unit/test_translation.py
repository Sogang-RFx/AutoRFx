"""Why a clause has no translation: the status and note recorded per clause."""

from __future__ import annotations

import logging
import threading

import pytest
from pydantic import ValidationError

from autorfx.analysis import (
    Answer,
    ClauseInput,
    OpenAIProvider,
    RuleOnlyProvider,
    ScriptedProvider,
    TransientProviderError,
    analyze,
    engine_ids,
    make_provider,
)
from autorfx.analysis.llm import openai_translation_call, translation_options, usage_from_metadata
from autorfx.analysis.translation import (
    WITHHELD,
    ClauseTranslation,
    SentenceTranslation,
    TranslationBatch,
    batches,
    clean,
    translate_clause,
)
from autorfx.analysis.usage import Usage, UsageMeter
from autorfx.config import Settings
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


# ── fix round 1 ─────────────────────────────────────────────────────────────
class CountingProvider(ScriptedProvider):
    """A double that reports usage, as a real client does."""

    def translate(self, clause, numbers):  # type: ignore[no-untyped-def]
        batch, _ = super().translate(clause, numbers)
        return batch, Usage(calls=1, input_tokens=10 * len(numbers), output_tokens=3)


def has_key(node: object, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(has_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(has_key(v, key) for v in node)
    return False


def test_batch_schema_is_strict_and_has_no_defaults():
    """TRANS-01: items is required; a default would put "default" into the strict schema."""
    from openai.lib._pydantic import to_strict_json_schema

    assert not has_key(to_strict_json_schema(TranslationBatch), "default")
    with pytest.raises(ValidationError):
        TranslationBatch.model_validate({})


def test_an_unrequested_number_in_a_reply_is_dropped():
    """TRANS-02: sentence 21 was not in the first batch; its text there must not be used."""
    clause = many(25)
    first = TranslationBatch(items=tuple(SentenceTranslation(n=n, text="문장.") for n in range(1, 22)))
    got, _ = run(clause, [first, TransientProviderError("TimeoutError")])
    assert got.sentences[20] is None and got.sentences[0] == "문장."


def test_a_blank_first_duplicate_wins_and_the_number_is_asked_again():
    """TRANS-02: the first occurrence of a number counts, even blank; so the number is absent."""
    clause = make_clause("6.1", ["Ship by train."])
    first = TranslationBatch(
        items=(SentenceTranslation(n=1, text=" "), SentenceTranslation(n=1, text="열차."))
    )
    got, provider = run(clause, [first, {1: "열차 후."}])
    assert got.sentences == ("열차 후.",) and len(provider.translate_calls) == 2


def test_a_marker_only_reply_is_missing_and_asked_again():
    """TRANS-02, CITE-02: one definition of blank, after marker cleaning."""
    clause = make_clause("6.2", ["Ship by train."])
    got, provider = run(clause, [{1: " [S1] "}, {1: "열차."}])
    assert got.status == "translated" and len(provider.translate_calls) == 2
    never, _ = run(clause, [{1: "[S1]"}])
    assert never.sentences == (None,) and never.note == "missing=1"


def test_markers_leave_no_stray_spaces():
    """CITE-02: a marker before a full stop or next to another marker leaves clean text."""
    assert clean("FCA이다 [S1].", "plain") == "FCA이다."
    assert clean("A [S1][S2] B", "plain") == "A B"
    assert clean("[S1] A [S2]", "plain") == "A"
    assert clean("A [S1] B [S2]", "has [S1]") == "A [S1] B"


def test_only_the_marker_the_source_lacks_is_removed():
    """CITE-02: the source has [S1]; the copied [S2] goes, [S1] stays."""
    clause = make_clause("6.3", ["See [S1] of the annex."])
    got, _ = run(clause, [{1: "[S2] 부속서 [S1] 참조."}])
    assert got.sentences == ("부속서 [S1] 참조.",)


@pytest.mark.parametrize("queue", [[ValueError("boom")], [{}, ValueError("boom")]])
def test_a_non_transient_exception_propagates(queue: list):
    """TRANS-03: only TransientProviderError is handled; a bug is not hidden in a note."""
    clause = make_clause("6.4", ["Ship by train."])
    with pytest.raises(ValueError):
        run(clause, queue)


def test_two_added_numbers_in_one_sentence_count_two():
    """CITE-02: the note counts added numbers, not sentences."""
    clause = make_clause("6.5", ["Ship by train."])
    got, _ = run(clause, [{1: "열차 7대 8일."}])
    assert got.status == "withheld" and got.note == "extra_numbers=2"


def test_usage_is_summed_over_every_call():
    """TRANS-04: batches and retries add up."""
    clause = many(21)
    provider = CountingProvider(translations={clause.clause_id: [{n: "문장." for n in range(1, 20)}, {}]})
    got = translate_clause(clause, provider)
    assert len(provider.translate_calls) == 4, "batch, retry of 20, batch 2, retry of 21"
    assert got.usage == Usage(calls=4, input_tokens=230, output_tokens=12)


def test_a_withheld_translation_is_not_logged(caplog: pytest.LogCaptureFixture):
    """TRANS-03: logs carry ids and counts, never the translation."""
    clause = make_clause("6.6", ["Ship by train."])
    with caplog.at_level(logging.DEBUG):
        run(clause, [{1: "비밀번역 777"}])
    assert caplog.records and all("비밀번역" not in r.getMessage() for r in caplog.records)


# ── separate translation model and token usage (TRANS-03, INV-12) ───────────
USED = Usage(calls=1, input_tokens=100, cached_input_tokens=10, output_tokens=40, reasoning_tokens=5)


def test_engines_follow_the_translation_model(ruleset: Ruleset):
    """ENGINE-03: without the variable translation is the judgement model; with it only translation moves."""
    plain = make_provider(Settings(_env_file=None, OPENAI_API_KEY="sk-test-not-real"), ruleset)  # type: ignore[call-arg]
    assert engine_ids(plain) == {
        "analysis": "openai:gpt-4o",
        "translation": "openai:gpt-4o",
        "llm_connected": True,
    }
    split = make_provider(
        Settings(_env_file=None, OPENAI_API_KEY="sk-test-not-real", OPENAI_TRANSLATION_MODEL="gpt-x"),  # type: ignore[call-arg]
        ruleset,
    )
    assert engine_ids(split) == {
        "analysis": "openai:gpt-4o",
        "translation": "openai:gpt-x",
        "llm_connected": True,
    }
    assert RuleOnlyProvider().translation_id is None
    assert engine_ids(RuleOnlyProvider())["translation"] == "none"


def test_judgement_calls_never_use_the_translation_model(ruleset: Ruleset):
    """TRANS-03: confirm and answer use the judgement call; translate uses the translation call only."""
    judged: list[type] = []
    translated: list[type] = []

    def judge(system: str, user: str, schema: type):
        judged.append(schema)
        return Answer(text="답", citations=(1,), answered=True)

    def translate(system: str, user: str, schema: type):
        translated.append(schema)
        return TranslationBatch(items=(SentenceTranslation(n=1, text="번역"),)), USED

    provider = OpenAIProvider(ruleset, "gpt-4o", judge, translation_model="gpt-x", translation_call=translate)
    assert provider.id == "openai:gpt-4o" and provider.translation_id == "openai:gpt-x"
    batch, usage = provider.translate(ClauseInput.of(PLAIN), (1,))
    provider.answer("q", [], None)
    assert translated == [TranslationBatch] and judged == [Answer]
    assert batch.items[0].text == "번역" and usage == USED


def test_a_reply_without_usage_counts_one_call(ruleset: Ruleset):
    """INV-12: without a translation call the judgement call is wrapped; the call is counted, tokens are 0."""

    def judge(system: str, user: str, schema: type):
        return TranslationBatch(items=(SentenceTranslation(n=1, text="번역"),))

    provider = OpenAIProvider(ruleset, "gpt-4o", judge)
    assert provider.translation_id == "openai:gpt-4o"
    _, usage = provider.translate(ClauseInput.of(PLAIN), (1,))
    assert usage == Usage(calls=1)
    assert usage_from_metadata(None) == Usage(calls=1)
    assert usage_from_metadata({}) == Usage(calls=1)


def test_usage_is_read_from_langchain_metadata():
    """INV-12: input, output, cached input and reasoning tokens; missing parts are 0."""
    meta = {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "input_token_details": {"cache_read": 64},
        "output_token_details": {"reasoning": 7},
    }
    assert usage_from_metadata(meta) == Usage(
        calls=1, input_tokens=120, cached_input_tokens=64, output_tokens=30, reasoning_tokens=7
    )
    assert usage_from_metadata({"input_tokens": 3, "output_tokens": 4}) == Usage(
        calls=1, input_tokens=3, output_tokens=4
    )


def test_translation_client_options():
    """TRANS-03: temperature 0 only for gpt-4*, effort only when set, retries stay 2."""
    gpt4 = translation_options("gpt-4o", "k", None, 60.0)
    assert gpt4 == {"model": "gpt-4o", "api_key": "k", "timeout": 60.0, "max_retries": 2, "temperature": 0}
    luna = translation_options("gpt-6-luna", "k", "none", 30.0)
    assert luna == {
        "model": "gpt-6-luna",
        "api_key": "k",
        "timeout": 30.0,
        "max_retries": 2,
        "reasoning_effort": "none",
    }
    assert "reasoning_effort" not in translation_options("gpt-5.1", "k", None, 60.0)


class FakeChat:
    """Stands in for ChatOpenAI: returns the queued include_raw reply."""

    reply: dict = {}
    seen: dict = {}

    def __init__(self, **kwargs):
        FakeChat.seen = kwargs

    def with_structured_output(self, schema, include_raw=False):
        assert include_raw is True
        return self

    def invoke(self, messages):
        return FakeChat.reply


class RawMessage:
    usage_metadata = {"input_tokens": 50, "output_tokens": 9, "output_token_details": {"reasoning": 2}}


def test_translation_call_reads_usage_and_raises_on_a_parsing_error(monkeypatch: pytest.MonkeyPatch):
    """INV-12: a good reply carries usage; a parsing error is transient but its tokens were billed."""
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChat)
    call = openai_translation_call("gpt-x", "k", "none")
    batch = TranslationBatch(items=(SentenceTranslation(n=1, text="번역"),))
    billed = Usage(calls=1, input_tokens=50, output_tokens=9, reasoning_tokens=2)
    FakeChat.reply = {"raw": RawMessage(), "parsed": batch, "parsing_error": None}
    parsed, usage = call("sys", "user", TranslationBatch)
    assert parsed == batch and usage == billed
    assert FakeChat.seen["reasoning_effort"] == "none" and "temperature" not in FakeChat.seen

    for bad in (
        {"raw": RawMessage(), "parsed": None, "parsing_error": ValueError("secret text")},
        {"raw": RawMessage(), "parsed": None, "parsing_error": None},
    ):
        FakeChat.reply = bad
        with pytest.raises(TransientProviderError) as caught:
            call("sys", "user", TranslationBatch)
        assert str(caught.value) == "ParsingError" and caught.value.usage == billed


def test_provider_keeps_a_transient_error_as_it_is(ruleset: Ruleset):
    """INV-12: wrapping must not rename ParsingError or drop the billed usage."""

    def translate(system: str, user: str, schema: type):
        raise TransientProviderError("ParsingError", usage=USED)

    provider = OpenAIProvider(ruleset, "gpt-4o", lambda *a: None, translation_call=translate)
    with pytest.raises(TransientProviderError) as caught:
        provider.translate(ClauseInput.of(PLAIN), (1,))
    assert str(caught.value) == "ParsingError" and caught.value.usage == USED


def test_billed_usage_of_a_failed_call_is_kept():
    """INV-12: a failed first call and a failed retry both add their usage to the clause."""
    first = ScriptedProvider(translations={"d01:1.1": [TransientProviderError("ParsingError", usage=USED)]})
    result = translate_clause(PLAIN, first)
    assert result.status == "failed" and result.usage == USED

    retry = ScriptedProvider(
        translations={"d01:1.1": [{9: "x"}, TransientProviderError("ParsingError", usage=USED)]},
        usage=USED,
    )
    result = translate_clause(PLAIN, retry)
    assert result.usage == USED.plus(USED)


def test_meter_adds_up_across_threads():
    """INV-12: four threads add 1,000 usages each and nothing is lost."""
    meter = UsageMeter()
    one = Usage(calls=1, input_tokens=3, cached_input_tokens=1, output_tokens=2, reasoning_tokens=1)

    def work() -> None:
        for _ in range(1000):
            meter.add(one)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert meter.total() == Usage(
        calls=4000, input_tokens=12000, cached_input_tokens=4000, output_tokens=8000, reasoning_tokens=4000
    )


def test_analyze_meters_every_clause_including_failed_ones(ruleset: Ruleset):
    """INV-12: tokens of withheld and failed clauses are counted; skipped clauses cost nothing."""
    provider = ScriptedProvider(
        translations={
            "d01:1.1": ["인도조건은 FCA이다."],
            "d01:1.4": [TransientProviderError("ParsingError", usage=USED)],
            "d01:1.5": ["샘플은 999개이다."],
        },
        usage=USED,
    )
    meter = UsageMeter()
    results = by_id(analyze(CLAUSES, ruleset, provider, meter=meter))
    assert results["d01:1.4"].translation_status == "failed"
    assert meter.total() == USED.plus(USED).plus(USED)  # 1.1 ok, 1.4 failed (error usage), 1.5 withheld
    analyze([PLAIN], ruleset, RuleOnlyProvider(), meter=meter)
    assert meter.total().calls == 3
