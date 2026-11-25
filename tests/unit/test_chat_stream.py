"""Chat progress events: the service observer, the statement counter and the streaming model call.

docs/design/immersive-ui.md §5, §5.1, §5.2. The observer only watches: what it sees is stages and counts,
never the words of a draft that has not been verified (CITE-01, CITE-02, INV-3). No network.
"""

from __future__ import annotations

import json
from importlib import import_module

import pytest

from autorfx.analysis import TransientProviderError
from autorfx.chat.model import OpenAIChatModel, ScriptedChatModel
from autorfx.chat.models import ChatContext, Draft
from autorfx.rules import Ruleset
from chat_helpers import WARRANTY, dumped, make_context
from chat_helpers import draft as make_draft
from chat_stream_helpers import (
    BAD,
    DOWN,
    DRAFT_TEXTS,
    EVENT_KEYS,
    EVIDENCE,
    FREE,
    GENERATED,
    GENERATIVE,
    GOOD,
    QUESTION,
    ROUTES,
    STAGES,
    Call,
    Route,
    StreamCall,
    count,
    plan,
    progress_runs,
    run,
    scripted,
    shape,
    watch,
)
from factories import make_analysis

ROUTE_IDS = [route.name for route in ROUTES]


@pytest.fixture(scope="module")
def context(ruleset: Ruleset) -> ChatContext:
    return make_context(ruleset, analyses=[make_analysis(WARRANTY, label="무제한 보증", reason="r")])


def route_named(name: str) -> Route:
    return next(route for route in ROUTES if route.name == name)


# ── the order of events (§5.1) ──────────────────────────────────────────────
@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_every_path_reports_its_stages_in_the_agreed_order(context: ChatContext, route: Route):
    """§5.1, CITE-01: the first event is ``plan``; each path then shows the stages the table names."""
    events, _, _ = watch(context, route)
    assert events and events[0] == {"type": "stage", "stage": "plan"}
    assert shape(events) == route.shape


@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_the_observer_gets_stage_and_progress_events_only(context: ChatContext, route: Route):
    """§5.2: ``done`` and ``error`` belong to the HTTP layer; the service sends two kinds of event."""
    events, _, _ = watch(context, route)
    for event in events:
        assert set(event) <= EVENT_KEYS[event["type"]], event
        assert event.get("stage", "plan") in STAGES


def test_write_carries_the_number_of_evidence_sentences_the_model_was_given(context: ChatContext):
    """§5.1: "근거 문장 수" is what the model got, not what was searched."""
    events, _, model = watch(context, route_named("generated"))
    (write,) = [e for e in events if e.get("stage") == "write"]
    assert model is not None and len(model.answer_calls) == 1
    assert write["evidence"] == len(model.answer_calls[0][1]) > 0


def test_progress_counts_the_statements_written_and_starts_again_after_a_rewrite(context: ChatContext):
    """§5.1: ``drafted`` never decreases within one draft; after ``rewrite`` it is counted from the start."""
    events, _, model = watch(context, route_named("rewritten"))
    assert model is not None and len(model.answer_calls) == 2
    first, second = progress_runs(events)
    for run_ in (first, second):
        assert run_ == sorted(run_)
    assert [n for n in first if n] == [1, 2], "the first draft has two statements"
    assert [n for n in second if n] == [1, 2], "the rewrite is counted from the start, not from 3"


def test_each_draft_is_counted_by_its_own_length(context: ChatContext):
    """§5.1: a rewrite of three statements after a draft of one counts 1, 2, 3, not 2, 3, 4."""
    retry = Route("retry", QUESTION, GENERATED, scripted(make_draft(BAD), make_draft(GOOD, FREE, GOOD)))
    first, second = progress_runs(watch(context, retry)[0])
    assert [n for n in first if n] == [1] and [n for n in second if n] == [1, 2, 3]


# ── nothing is said before it is verified ───────────────────────────────────
@pytest.mark.parametrize("route", GENERATIVE, ids=[r.name for r in GENERATIVE])
def test_no_draft_text_reaches_the_observer(context: ChatContext, route: Route):
    """CITE-01, CITE-02, INV-3: not even a draft the citation check rejects later is shown as text."""
    events, _, _ = watch(context, route)
    seen = dumped(events)
    assert not [text for text in DRAFT_TEXTS if text in seen]
    assert not [word for word in ("36개월", "24개월", "무상") if word in seen]


@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_the_observer_sees_no_sentence_of_the_package(context: ChatContext, route: Route):
    """INV-8: events are names and numbers; a clause sentence, quoted or not, is never one of them."""
    events, _, _ = watch(context, route)
    seen = dumped(events)
    for clause in context.clauses:
        assert not [s.text for s in clause.sentences if s.text in seen]
    assert "1.2345" not in seen


# ── the same answer ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_an_observer_does_not_change_the_answer(context: ChatContext, route: Route):
    """§5: the stream watches the one path that makes an answer; it does not take a second one."""
    plain, plain_model = run(context, route)
    watched, watched_model = run(context, route, observer=lambda event: None)
    assert watched == plain
    if plain_model is not None and watched_model is not None:
        assert watched_model.answer_calls == plain_model.answer_calls
        assert watched_model.plan_calls == plain_model.plan_calls


@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_an_observer_that_raises_changes_nothing_and_does_not_raise(context: ChatContext, route: Route):
    """§5.2, ENGINE-02: a broken listener is not a reason to lose an answer."""
    calls: list[dict] = []

    def broken(event: dict) -> None:
        calls.append(event)
        raise RuntimeError("the listener is gone: 1.2345 EUR")

    plain, _ = run(context, route)
    answered, _ = run(context, route, observer=broken)
    assert answered == plain
    assert calls, "it was called, and its failure was swallowed"


def test_the_service_still_works_with_a_model_that_knows_nothing_of_progress(context: ChatContext):
    """§5.2: without an observer ``answer`` is called as before, so a model without ``on_progress`` works."""

    class Old:
        id = "old"

        def plan(self, question, history, languages):
            return plan("lookup", question)

        def answer(self, question, evidence, *, overview, feedback):
            number = next(e.number for e in evidence if "Gewährleistungsfrist" in e.text)
            return make_draft(("보증 기간은 36개월입니다.", (number,)))

    data, _ = run(context, route_named("generated"), model=Old())  # type: ignore[arg-type]
    assert data["mode"] == "generative"


# ── count_statements (§5.2) ─────────────────────────────────────────────────
HEAD = '{"answered":true,"complete":true,"statements":['
TRICKY = (
    'He said \\"stop}\\" and {x] [1] \\"text\\": 5 \\\\ 끝',
    "plain",
    '{\\"text\\": \\"nested\\"} \\"',
    "한글 \\u00e9 and \\n newline",
)


def document(texts: tuple[str, ...], *, citations_first: bool = False) -> tuple[str, list[int]]:
    """A draft as the model writes it, and the offset just after each statement's closing brace."""
    parts, ends, text = [], [], HEAD
    for n, body in enumerate(texts):
        cites = f'"citations":[{n + 1},{n + 2}]'
        item = "{" + (f'{cites},"text":"{body}"' if citations_first else f'"text":"{body}",{cites}') + "}"
        text += ("," if parts else "") + item
        parts.append(item)
        ends.append(len(text))
    return text + '],"extra":"]"}', ends


def test_nothing_written_yet_counts_none():
    assert count("") == 0
    assert count("{") == 0
    assert count('{"answered":true,"complete":true,"statements":[') == 0
    assert count('{"answered":true,"complete":true,"statements":[]}') == 0


def test_a_half_written_statement_is_not_counted():
    assert count(HEAD + '{"text":"보증 기') == 0
    assert count(HEAD + '{"text":"보증 기간","citations":[1') == 0
    assert count(HEAD + '{"text":"보증 기간","citations":[1]') == 0, "the object is not closed yet"


def test_closed_statements_are_counted_and_the_open_one_is_not():
    one = '{"text":"가","citations":[1]}'
    assert count(HEAD + one) == 1
    assert count(HEAD + one + ",") == 1
    assert count(HEAD + one + ',{"text":"나"') == 1
    assert count(HEAD + one + "," + one + "," + one) == 3


def test_quotes_braces_brackets_and_a_text_key_inside_a_string_do_not_count():
    """§5.2: "문자열 안의 따옴표와 중괄호에 속지 않는다"."""
    opened = HEAD + '{"text":"a \\"quoted\\" } ] \\"text\\": 1 {'
    assert count(opened) == 0
    assert count(opened + '","citations":[1]}') == 1
    assert count(HEAD + '{"text":"x \\" } {\\"text\\":\\"y\\"}","citations":[1]}') == 1
    assert count(HEAD + '{"text":"{\\"text\\": [1]}"') == 0


def test_an_input_cut_in_the_middle_of_an_escape_is_counted_without_failing():
    one = '{"text":"가","citations":[1]}'
    for cut in ("\\", "\\u", "\\u00", '\\"'):
        assert count(HEAD + one + ',{"text":"나' + cut) == 1
    assert count(HEAD + '{"text":"a\\') == 0


@pytest.mark.parametrize("citations_first", [False, True], ids=["text-first", "citations-first"])
def test_every_prefix_of_a_document_counts_exactly_the_statements_closed_so_far(citations_first: bool):
    """§5.2: the count only rises, one statement at a time, and ends at the number of statements."""
    text, ends = document(TRICKY, citations_first=citations_first)
    assert len(json.loads(text)["statements"]) == len(TRICKY), "the document is valid JSON"
    previous = 0
    for cut in range(len(text) + 1):
        expected = sum(1 for end in ends if end <= cut)
        found = count(text[:cut])
        assert found == expected, f"{text[:cut]!r}: {found} != {expected}"
        assert found >= previous
        previous = found


def test_the_complete_document_counts_every_statement():
    full = Draft(
        answered=True, complete=True, statements=make_draft(GOOD, FREE, ("셋", (3,))).statements
    ).model_dump_json()
    assert count(full) == 3
    assert count(Draft(answered=False, complete=True, statements=()).model_dump_json()) == 0


# ── ScriptedChatModel ───────────────────────────────────────────────────────
def test_the_scripted_model_reports_one_count_per_statement_of_its_draft():
    """§5.2: 1, 2, ... n for the prepared draft; an exception is raised before any count."""
    seen: list[int] = []
    model = ScriptedChatModel(drafts=[make_draft(GOOD, FREE, GOOD), DOWN, make_draft()])
    result = model.answer("q", EVIDENCE, overview=False, feedback=None, on_progress=seen.append)
    assert seen == [1, 2, 3] and len(result.statements) == 3
    seen.clear()
    with pytest.raises(TransientProviderError):
        model.answer("q", EVIDENCE, overview=False, feedback=None, on_progress=seen.append)
    model.answer("q", EVIDENCE, overview=False, feedback=None, on_progress=seen.append)
    assert seen == [], "no statements, no counts"
    assert [call[3] for call in model.answer_calls] == [None, None, None], "the calls keep their shape"


def test_the_scripted_model_needs_no_listener():
    model = ScriptedChatModel(drafts=[make_draft(GOOD)])
    assert len(model.answer("q", EVIDENCE, overview=False, feedback=None, on_progress=None).statements) == 1


# ── OpenAIChatModel (§5.2) ──────────────────────────────────────────────────
TWO = make_draft(GOOD, FREE)
PLAIN_REPLY = make_draft(("보증은 일반 호출의 답입니다.", (1,)))


def build(stream: StreamCall | None, plain: Call | None = None) -> tuple[OpenAIChatModel, Call]:
    plain = plain or Call(PLAIN_REPLY)
    unused = Call(PLAIN_REPLY)
    model = OpenAIChatModel("gpt-test", plan_call=unused, answer_call=plain, answer_stream_call=stream)
    return model, plain


def test_without_a_listener_only_the_plain_call_is_used():
    """§5.2: the same path as before this feature."""
    stream = StreamCall(TWO)
    model, plain = build(stream)
    assert model.answer("q", EVIDENCE, overview=False, feedback=None) == PLAIN_REPLY
    assert len(plain.seen) == 1 and stream.seen == []


def test_a_listener_without_a_streaming_call_uses_the_plain_call():
    model, plain = build(None)
    seen: list[int] = []
    assert model.answer("q", EVIDENCE, overview=False, feedback=None, on_progress=seen.append) == PLAIN_REPLY
    assert len(plain.seen) == 1 and seen == []


def test_with_a_listener_the_streaming_call_runs_and_counts_rise_as_chunks_arrive():
    """§5.2: ``on_progress`` hears a statement when its closing brace arrives, not after the call ends."""
    seen: list[int] = []
    stream = StreamCall(TWO, watch=seen)
    model, plain = build(stream)
    assert model.answer("q", EVIDENCE, overview=False, feedback=None, on_progress=seen.append) == TWO
    assert plain.seen == [] and len(stream.seen) == 1
    assert seen == [1, 2]
    assert [1] in stream.snapshots, "the first count came while the document was still being written"
    assert stream.snapshots[-1] == [1, 2] and len(stream.snapshots) > 3


def test_the_streaming_call_gets_the_same_prompt_and_schema_as_the_plain_call():
    """§5: one way to make an answer, watched."""
    stream = StreamCall(TWO)
    model, plain = build(stream)
    model.answer("보증 기간은?", EVIDENCE, overview=True, feedback="문장 1의 24", on_progress=lambda n: None)
    model.answer("보증 기간은?", EVIDENCE, overview=True, feedback="문장 1의 24")
    assert stream.seen == plain.seen and stream.seen[0][2] is Draft


@pytest.mark.parametrize(
    "failure", [ValueError("bad json: 1.2345"), RuntimeError("closed")], ids=["value", "runtime"]
)
def test_a_streaming_failure_that_is_not_an_outage_is_asked_again_once_without_streaming(failure: Exception):
    """§5: streaming is an extra. When it fails for another reason than the provider being down, the answer is
    still made, by the call that always worked."""
    seen: list[int] = []
    stream = StreamCall(failure)
    model, plain = build(stream)
    assert model.answer("q", EVIDENCE, overview=False, feedback=None, on_progress=seen.append) == PLAIN_REPLY
    assert len(stream.seen) == 1 and len(plain.seen) == 1


@pytest.mark.parametrize(
    "failure", [TimeoutError("slow"), ConnectionError("down")], ids=["timeout", "connection"]
)
def test_a_streaming_outage_is_not_retried(failure: Exception):
    """ENGINE-02: a provider that is down is not asked a second time within the same answer."""
    stream = StreamCall(failure)
    model, plain = build(stream)
    with pytest.raises(TransientProviderError) as raised:
        model.answer("q", EVIDENCE, overview=False, feedback=None, on_progress=lambda n: None)
    assert len(stream.seen) == 1 and plain.seen == []
    assert "slow" not in str(raised.value) and "down" not in str(raised.value)


def test_a_failing_plain_call_after_the_fallback_is_transient():
    model, plain = build(StreamCall(ValueError("x")), Call(ValueError("y")))
    with pytest.raises(TransientProviderError):
        model.answer("q", EVIDENCE, overview=False, feedback=None, on_progress=lambda n: None)
    assert len(plain.seen) == 1


# ── is_outage (§5.2) ────────────────────────────────────────────────────────
def outage(exc: BaseException) -> bool:
    return import_module("autorfx.chat.model").is_outage(exc)


@pytest.mark.parametrize("exc", [TimeoutError("t"), ConnectionError("c"), ConnectionResetError("r")])
def test_timeouts_and_lost_connections_are_outages(exc: BaseException):
    assert outage(exc) is True


@pytest.mark.parametrize("exc", [ValueError("v"), RuntimeError("r"), KeyError("k"), TypeError("t")])
def test_other_failures_are_not_outages(exc: BaseException):
    assert outage(exc) is False


def test_the_http_and_sdk_exceptions_with_the_same_meaning_are_outages():
    """§5.2: "httpx와 OpenAI SDK의 같은 뜻의 예외"."""
    import httpx
    import openai

    request = httpx.Request("POST", "http://localhost/v1")
    assert outage(httpx.ConnectError("refused", request=request))
    assert outage(httpx.ReadTimeout("slow", request=request))
    assert outage(openai.APITimeoutError(request))
    assert outage(openai.APIConnectionError(request=request))
    assert not outage(httpx.DecodingError("bad body", request=request))
