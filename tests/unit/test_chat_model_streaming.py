"""The real streaming call of the chat model, run through a fake OpenAI server (immersive-ui.md §5.2).

``langchain_openai`` and the OpenAI SDK are the real ones; only the HTTP transport is a double. This is where
three things live that fakes of the call itself cannot show: what the libraries print, which of their
exceptions mean "the provider", and how long a stream may run. No key, no network; the text is invented.
"""

from __future__ import annotations

import logging
from itertools import count

import pytest

from autorfx.analysis import TransientProviderError
from autorfx.chat.model import OpenAIChatModel, chat_stream_call
from autorfx.chat.models import Draft, Evidence, Statement
from chat_stream_helpers import Call
from factories import make_locus
from openai_fakes import FakeServer, chat_stream_body, error_response, refusal_stream_body, stream_response

KEY = "sk-test-not-real"
TIMEOUT = 5.0
DRAFT = Draft(
    answered=True,
    complete=True,
    statements=(
        Statement(text="납기는 1.2345일입니다.", citations=(1,)),
        Statement(text="비공개값 ZETA-77 을 확인합니다.", citations=(1,)),
    ),
)
DOCUMENT = DRAFT.model_dump_json()
DRAFT_FRAGMENTS = ("1.2345", "ZETA-77", "비공개값", "납기는")
EVIDENCE = (
    Evidence(number=1, text="Die Frist beträgt 36 Monate.", locus=make_locus(1), file="a.pdf", title="T"),
)
PLAIN = Draft(
    answered=True, complete=True, statements=(Statement(text="일반 호출의 답입니다.", citations=(1,)),)
)


def streaming(server: FakeServer, *, timeout: float = TIMEOUT, **options: object):  # type: ignore[no-untyped-def]
    return chat_stream_call(
        "gpt-test", KEY, timeout=timeout, effort=None, http_client=server.client, **options
    )  # type: ignore[arg-type]


def model_for(
    server: FakeServer, plain: Call | None = None, **options: object
) -> tuple[OpenAIChatModel, Call]:
    plain = plain or Call(PLAIN)
    unused = Call(PLAIN)
    model = OpenAIChatModel(
        "gpt-test", plan_call=unused, answer_call=plain, answer_stream_call=streaming(server, **options)
    )
    return model, plain


def ask(model: OpenAIChatModel, seen: list[int] | None = None) -> Draft:
    listener = (seen if seen is not None else []).append
    return model.answer("보증 기간은?", EVIDENCE, overview=False, feedback=None, on_progress=listener)


# ── 1. what the libraries print ─────────────────────────────────────────────
def test_the_streaming_call_returns_the_parsed_draft_and_hands_on_every_piece():
    server = FakeServer(lambda request: stream_response(chat_stream_body(DOCUMENT)))
    pieces: list[str] = []
    reply = streaming(server)("sys", "user", Draft, pieces.append)
    assert reply == DRAFT
    assert "".join(pieces) == DOCUMENT and len(pieces) > 5


def test_no_text_of_the_draft_reaches_warnings_stderr_or_the_log(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
):
    """The SDK's events carry the parsed draft and Pydantic warned about it, quoting its end. A draft is not
    verified yet and holds document values: it must not be printed anywhere."""
    import warnings

    server = FakeServer(lambda request: stream_response(chat_stream_body(DOCUMENT)))
    call = streaming(server)
    with warnings.catch_warnings(record=True) as caught, caplog.at_level(logging.DEBUG):
        for _ in range(3):  # the warning is raised once per streamed event, and the same place repeats
            assert call("sys", "user", Draft, lambda piece: None) == DRAFT
    printed = capsys.readouterr()
    assert [str(w.message) for w in caught] == []
    everything = printed.out + printed.err + "\n".join(r.getMessage() for r in caplog.records)
    assert not [part for part in (*DRAFT_FRAGMENTS, "Draft(", "serializer") if part in everything]


def test_the_warning_is_silenced_for_that_message_only():
    """Exactly one warning is filtered; another ``UserWarning`` still gets through."""
    import warnings

    streaming(FakeServer(lambda request: stream_response(b"")))
    with warnings.catch_warnings(record=True) as caught:
        warnings.warn("something else entirely", UserWarning, stacklevel=1)
    assert [str(w.message) for w in caught] == ["something else entirely"]


# ── 2. one more call only when the failure was ours ─────────────────────────
@pytest.mark.parametrize("status", [429, 503, 500, 400, 401])
def test_an_http_error_of_the_provider_costs_no_second_call(status: int):
    """The SDK has already retried once; asking again through the plain call would double the wait."""
    server = FakeServer(lambda request: error_response(status, "limit reached for org-private"))
    model, plain = model_for(server)
    with pytest.raises(TransientProviderError) as raised:
        ask(model)
    assert plain.seen == []
    assert "org-private" not in str(raised.value) and "limit" not in str(raised.value)


def test_a_refusal_is_not_asked_again():
    server = FakeServer(lambda request: stream_response(refusal_stream_body()))
    model, plain = model_for(server)
    with pytest.raises(TransientProviderError):
        ask(model)
    assert plain.seen == []


@pytest.mark.parametrize("finish", ["length", "content_filter"])
def test_a_cut_off_answer_is_not_asked_again(finish: str):
    server = FakeServer(lambda request: stream_response(chat_stream_body(DOCUMENT[:20], finish=finish)))
    model, plain = model_for(server)
    with pytest.raises(TransientProviderError):
        ask(model)
    assert plain.seen == []


def test_a_reply_that_does_not_parse_is_ours_and_is_asked_again_once_without_streaming():
    """Parsing is our side of the streaming path: the plain call does not share it."""
    server = FakeServer(lambda request: stream_response(chat_stream_body("this is not json")))
    model, plain = model_for(server)
    assert ask(model) == PLAIN
    assert len(plain.seen) == 1


def test_a_library_that_cannot_stream_is_asked_again_once_without_streaming():
    def broken(system: str, user: str, schema: type, on_token):  # type: ignore[no-untyped-def]
        raise AttributeError("module has no attribute 'beta'")

    model = OpenAIChatModel(
        "gpt-test", plan_call=Call(PLAIN), answer_call=(plain := Call(PLAIN)), answer_stream_call=broken
    )
    assert ask(model) == PLAIN and len(plain.seen) == 1


def test_the_decision_is_named_for_what_it_decides():
    from autorfx.chat import model as chat_model

    ours = chat_model.worth_asking_without_streaming
    assert ours(ValueError("v")) and ours(AttributeError("a")) and ours(RuntimeError("r"))
    assert not ours(TimeoutError("t")) and not ours(ConnectionError("c"))
    assert not ours(chat_model.StreamDeadlineError("late"))
    assert chat_model.is_outage(chat_model.StreamDeadlineError("late")), "a deadline is a timeout"


# ── 3. a total time limit ───────────────────────────────────────────────────
class Clock:
    """Each look at the clock is ``step`` seconds later than the one before."""

    def __init__(self, step: float) -> None:
        self._ticks, self._step = count(), step

    def __call__(self) -> float:
        return next(self._ticks) * self._step


def test_a_stream_that_trickles_past_the_deadline_is_stopped_and_counts_as_a_timeout():
    """The client's timeout is per read; a stream that keeps sending is cut by the clock of the answer."""
    server = FakeServer(lambda request: stream_response(chat_stream_body(DOCUMENT, piece=2)))
    model, plain = model_for(server, timeout=10.0, clock=Clock(step=3.0))
    seen: list[int] = []
    with pytest.raises(TransientProviderError) as raised:
        ask(model, seen)
    assert plain.seen == [], "a deadline is a timeout: the provider is not asked a second time"
    assert str(raised.value) == "StreamDeadlineError"
    assert seen == [], "stopped before the first statement was complete"


def test_a_stream_within_the_deadline_is_not_touched():
    server = FakeServer(lambda request: stream_response(chat_stream_body(DOCUMENT)))
    model, plain = model_for(server, timeout=10.0, clock=Clock(step=0.0))
    assert ask(model) == DRAFT and plain.seen == []


def test_an_error_inside_the_listener_is_swallowed_and_the_answer_stays_the_same():
    """Only the deadline escapes the handler (``raise_error``); anything else in it changes nothing."""
    server = FakeServer(lambda request: stream_response(chat_stream_body(DOCUMENT)))
    call = streaming(server)
    heard: list[str] = []

    def failing(piece: str) -> None:
        heard.append(piece)
        raise RuntimeError("listener broke")

    assert call("sys", "user", Draft, failing) == DRAFT
    assert "".join(heard) == DOCUMENT, "a failing listener is still given every piece"
