"""``POST /rfqs/{id}/chat/stream``: the same question as ``/chat``, with its progress as server-sent events.

docs/design/immersive-ui.md §5, §5.1, §5.2. Failures before the stream opens are ordinary JSON errors; what
happens after is told as events. The cost rule holds for every byte of the stream (INV-8). No network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from auth_helpers import Accounts, running_app, settings_in
from auth_helpers import error_of as error
from autorfx.chat.models import Draft, Plan, Statement, Turn
from autorfx.rules import Ruleset
from chat_stream_helpers import ASKED_PRICE, QUESTION, parse_sse, shape
from contract_schema import assert_valid
from factories import PACKAGE_MAIN, PACKAGE_SECRET, make_package, pdf_from_html

FULL_VIEW, RESTRICTED = "sales", "logistics"
"""Everything; its own department without cost clauses."""
SECRETS = (PACKAGE_SECRET, "1.2345", "target price", "rejected")
STREAM = "/rfqs/{}/chat/stream"
ANSWER = "보증 기간은 36개월입니다."
LEAK = "provider said: prompt text with 1.2345 EUR"
Api = tuple[Accounts, str]


class Reader:
    """Cites the warranty sentence by the number it was given, and reports its one statement as written."""

    id = "reader"

    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.plans: list[tuple[str, tuple[Turn, ...]]] = []
        self.answers: list[str] = []

    def plan(self, question, history, languages):  # type: ignore[no-untyped-def]
        self.plans.append((question, tuple(history)))
        return Plan(intent="lookup", question=question, queries=())

    def answer(self, question, evidence, *, overview, feedback, on_progress=None):  # type: ignore[no-untyped-def]
        self.answers.append(question)
        if self.fail is not None:
            raise self.fail
        number = next(e.number for e in evidence if "36 months" in e.text)
        if on_progress is not None:
            on_progress(1)
        statement = Statement(text=ANSWER, citations=(number,))
        return Draft(answered=True, complete=True, statements=(statement,))


@pytest.fixture(scope="module")
def stream_api(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Api]:
    root = tmp_path_factory.mktemp("stream-api")
    with running_app(settings_in(root), ruleset) as accounts:
        yield accounts, accounts.upload(make_package(root / "upload"))


def open_stream(client: TestClient, rfq_id: str, body: dict):  # type: ignore[no-untyped-def]
    return client.post(STREAM.format(rfq_id), json=body)


def events_of(response) -> list[dict]:  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    for event in events:
        assert_valid("chat-stream-event", event)
    return events


# ── the wire ────────────────────────────────────────────────────────────────
def test_the_answer_comes_as_an_event_stream_that_ends_with_done(stream_api: Api):
    """§5: ``text/event-stream``, one ``data:`` JSON per event, and ``done`` is the last of them."""
    accounts, rfq_id = stream_api
    accounts.state.chat_model = Reader()
    response = open_stream(accounts[FULL_VIEW], rfq_id, {"question": QUESTION})
    assert response.headers["content-type"].startswith("text/event-stream")
    cache = {part.strip() for part in response.headers["cache-control"].split(",")}
    assert cache & {"no-cache", "no-store"}, "a stream must not be kept"
    events = events_of(response)
    assert events[0] == {"type": "stage", "stage": "plan"}
    assert [e["type"] for e in events].count("done") == 1 and events[-1]["type"] == "done"
    assert "error" not in {e["type"] for e in events}
    assert_valid("chat", events[-1]["response"])


def test_comment_lines_in_the_stream_are_not_events():
    """§5: a ``:`` line is a keep-alive for proxies; the reader skips it and a blank line ends an event."""
    plan, progress = '{"type":"stage","stage":"plan"}', '{"type":"progress","drafted":1}'
    raw = f": keep-alive\n\ndata: {plan}\n\n: ping\n\ndata: {progress}\n\n"
    assert parse_sse(raw) == [{"type": "stage", "stage": "plan"}, {"type": "progress", "drafted": 1}]


@pytest.mark.parametrize("model", ["none", "reader"])
def test_done_carries_the_body_of_the_plain_chat_answer(stream_api: Api, model: str):
    """§5: one path makes the answer; the stream watches it, so the two bodies are the same."""
    accounts, rfq_id = stream_api
    client, body = accounts[FULL_VIEW], {"question": QUESTION}
    accounts.state.chat_model = Reader() if model == "reader" else None
    streamed = events_of(open_stream(client, rfq_id, body))[-1]
    accounts.state.chat_model = Reader() if model == "reader" else None
    plain = client.post(f"/rfqs/{rfq_id}/chat", json=body)
    assert plain.status_code == 200 and streamed["type"] == "done"
    assert streamed["response"] == plain.json()
    assert streamed["response"]["data"]["mode"] == ("generative" if model == "reader" else "extractive")


def test_the_events_follow_the_agreed_order_over_http(stream_api: Api):
    """§5.1: no LLM: plan, search, verify. With one: plan, search, write (evidence), progress, verify."""
    accounts, rfq_id = stream_api
    accounts.state.chat_model = None
    plain = events_of(open_stream(accounts[FULL_VIEW], rfq_id, {"question": QUESTION}))
    assert shape(plain) == ("plan", "search", "verify", "done")
    accounts.state.chat_model = Reader()
    written = events_of(open_stream(accounts[FULL_VIEW], rfq_id, {"question": QUESTION}))
    assert shape(written) == ("plan", "search", "write", "progress", "verify", "done")
    (write,) = [e for e in written if e.get("stage") == "write"]
    assert isinstance(write["evidence"], int) and write["evidence"] > 0
    assert [e["drafted"] for e in written if e["type"] == "progress"] == [1]


def test_no_answer_text_is_in_the_stream_before_done(stream_api: Api):
    """CITE-01, §5: the words of the answer are in ``done`` and nowhere before it."""
    accounts, rfq_id = stream_api
    accounts.state.chat_model = Reader()
    events = events_of(open_stream(accounts[FULL_VIEW], rfq_id, {"question": QUESTION}))
    before = "".join(str(e) for e in events[:-1])
    assert ANSWER not in before and "개월" not in before
    assert ANSWER in str(events[-1]["response"])


def test_the_request_is_the_one_of_the_plain_chat(stream_api: Api):
    """§5: the same body: a follow-up arrives with its history and reaches the planner."""
    accounts, rfq_id = stream_api
    model = accounts.state.chat_model = Reader()
    turn = {"question": "보증 기간은?", "answer": "36개월입니다."}
    events_of(
        open_stream(accounts[FULL_VIEW], rfq_id, {"question": "그럼 그건 언제부터야?", "history": [turn]})
    )
    assert model.plans == [("그럼 그건 언제부터야?", (Turn(**turn),))]


# ── refused before the stream opens (§5) ────────────────────────────────────
def route_exists(accounts: Accounts, rfq_id: str) -> None:
    """A refusal means something only where the route exists: every unknown path is a 404 as well."""
    accounts.state.chat_model = None
    assert events_of(open_stream(accounts[FULL_VIEW], rfq_id, {"question": QUESTION}))[-1]["type"] == "done"


def refused(response, status: int, code: str) -> None:  # type: ignore[no-untyped-def]
    assert error(response) == (status, code)
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize(
    "body",
    [
        {"question": "  "},
        {"question": "x" * 501},
        {},
        {"question": QUESTION, "history": "보증"},
        {"question": QUESTION, "history": [{"question": "q", "answer": "a"}] * 7},
    ],
    ids=["blank", "too-long", "missing", "history-text", "history-seven"],
)
def test_a_malformed_question_is_a_json_error_not_a_stream(stream_api: Api, body: dict):
    """§5: 422 as on ``/chat``, and no event is sent."""
    accounts, rfq_id = stream_api
    accounts.state.chat_model = Reader()
    refused(open_stream(accounts[FULL_VIEW], rfq_id, body), 422, "invalid_request")


def test_without_a_session_there_is_no_stream(stream_api: Api):
    accounts, rfq_id = stream_api
    route_exists(accounts, rfq_id)
    refused(open_stream(accounts.anonymous(), rfq_id, {"question": QUESTION}), 401, "unauthenticated")


def test_a_foreign_origin_gets_no_stream(stream_api: Api):
    """The gate checks the origin of every POST, this one included."""
    accounts, rfq_id = stream_api
    route_exists(accounts, rfq_id)
    response = accounts[FULL_VIEW].post(
        STREAM.format(rfq_id), json={"question": QUESTION}, headers={"Origin": "https://example.org"}
    )
    refused(response, 403, "bad_origin")


def test_an_unknown_rfq_gets_no_stream(stream_api: Api):
    accounts, rfq_id = stream_api
    route_exists(accounts, rfq_id)
    refused(open_stream(accounts[FULL_VIEW], "rfq-doesnotexist", {"question": QUESTION}), 404, "not_found")


# ── cost visibility (INV-8) ─────────────────────────────────────────────────
def test_a_restricted_account_asking_for_the_price_gets_a_refusal_and_no_secret(stream_api: Api):
    """RAG-04, INV-4, INV-8: refused from the question alone; no byte of the stream carries the price."""
    accounts, rfq_id = stream_api
    model = accounts.state.chat_model = Reader()
    response = open_stream(accounts[RESTRICTED], rfq_id, {"question": ASKED_PRICE})
    events = events_of(response)
    assert events[-1]["type"] == "done" and events[-1]["response"]["data"]["status"] == "restricted"
    assert shape(events) == ("plan", "done")
    assert not [secret for secret in SECRETS if secret in response.text]
    assert model.answers == []


def test_a_restricted_account_gets_no_secret_in_an_answer_stream_either(stream_api: Api):
    """INV-8: stages, counts and the verified answer, and still no hidden sentence."""
    accounts, rfq_id = stream_api
    accounts.state.chat_model = Reader()
    response = open_stream(accounts[RESTRICTED], rfq_id, {"question": QUESTION})
    events = events_of(response)
    assert events[-1]["response"]["data"]["mode"] == "generative"
    assert not [secret for secret in SECRETS if secret in response.text]
    accounts.state.chat_model = None
    response = open_stream(accounts[RESTRICTED], rfq_id, {"question": "target price"})
    assert events_of(response)[-1]["type"] == "done"
    assert not [secret for secret in SECRETS if secret in response.text]


# ── a failure after the stream has opened (§5) ──────────────────────────────
def test_a_failure_in_the_answer_ends_the_stream_with_one_safe_error(stream_api: Api):
    """§5: ``error`` is the last event, instead of ``done``; it names neither the exception nor the prompt."""
    accounts, rfq_id = stream_api
    accounts.state.chat_model = Reader(fail=RuntimeError(LEAK))
    response = open_stream(accounts[FULL_VIEW], rfq_id, {"question": QUESTION})
    events = events_of(response)
    assert events[-1]["type"] == "error" and [e["type"] for e in events].count("error") == 1
    assert "done" not in {e["type"] for e in events}
    failure = events[-1]["error"]
    assert failure["code"] and failure["message"].strip()
    assert any("가" <= ch <= "힣" for ch in failure["message"]), "a message for the user, in Korean"
    for part in ("provider said", "1.2345", "RuntimeError", "prompt", "Traceback", "36 months"):
        assert part not in response.text
    assert events[0] == {"type": "stage", "stage": "plan"}


def test_the_server_still_answers_after_a_failed_stream(stream_api: Api):
    """§5.2: a failed or abandoned stream leaves nothing behind that breaks the next question."""
    accounts, rfq_id = stream_api
    accounts.state.chat_model = Reader(fail=RuntimeError(LEAK))
    open_stream(accounts[FULL_VIEW], rfq_id, {"question": QUESTION})
    accounts.state.chat_model = Reader()
    assert events_of(open_stream(accounts[FULL_VIEW], rfq_id, {"question": QUESTION}))[-1]["type"] == "done"
    assert accounts[FULL_VIEW].get("/health").json() == {"status": "ok"}


def test_the_stream_route_is_known_to_the_app(stream_api: Api):
    """§5.2: ``autorfx.api.chat_stream`` adds the route to the same app as the other chat route."""
    from auth_helpers import routes_of

    routes = routes_of(stream_api[0].app)
    assert ("POST", "/rfqs/{rfq_id}/chat/stream") in routes and ("POST", "/rfqs/{rfq_id}/chat") in routes


# ── the size of the body (§5.2) ─────────────────────────────────────────────
MAX_JSON_BYTES = 128 * 1024


def test_a_body_past_the_cap_is_413_by_its_declared_length(stream_api: Api):
    accounts, rfq_id = stream_api
    model = accounts.state.chat_model = Reader()
    response = open_stream(accounts[FULL_VIEW], rfq_id, {"question": "x" * MAX_JSON_BYTES})
    refused(response, 413, "too_large")
    assert model.plans == []


def test_a_body_past_the_cap_is_413_without_a_declared_length(stream_api: Api):
    accounts, rfq_id = stream_api
    model = accounts.state.chat_model = Reader()
    pieces = [b'{"question": "', b"x" * (MAX_JSON_BYTES + 1), b'"}']
    response = accounts[FULL_VIEW].post(
        STREAM.format(rfq_id), content=iter(pieces), headers={"content-type": "application/json"}
    )
    assert "content-length" not in response.request.headers
    refused(response, 413, "too_large")
    assert model.plans == []


def test_a_question_with_a_long_korean_history_fits_the_cap(stream_api: Api):
    """Everything the contract allows, in Korean (3 bytes a character): six turns of a 500-character
    question and a 4,000-character answer. The cap counts bytes, so it must not refuse what the limits in
    characters accept."""
    accounts, rfq_id = stream_api
    accounts.state.chat_model = None
    turn = {"question": "가" * 500, "answer": "나" * 4000}
    body = {"question": QUESTION, "history": [turn] * 6}
    assert 64 * 1024 < len(json.dumps(body, ensure_ascii=False).encode()) < MAX_JSON_BYTES
    assert events_of(open_stream(accounts[FULL_VIEW], rfq_id, body))[-1]["type"] == "done"


# ── INV-8: what the stream counts ───────────────────────────────────────────
HIDDEN_EXTRA = (
    "<p>2.4 The target price for the warranty period is 9.8765 EUR and the warranty reserve is rejected.</p>"
    "<p>2.5 A warranty period price list is attached to this target price.</p>"
)


def write_count(accounts: Accounts, rfq_id: str, who: str = RESTRICTED) -> int:
    accounts.state.chat_model = Reader()
    events = events_of(open_stream(accounts[who], rfq_id, {"question": "What is the warranty period?"}))
    (write,) = [e for e in events if e.get("stage") == "write"]
    return write["evidence"]


def test_the_evidence_count_of_a_restricted_account_does_not_depend_on_clauses_it_cannot_see(
    stream_api: Api, tmp_path
):  # type: ignore[no-untyped-def]
    """INV-8: the number in ``write`` is a fact about the account's own view; hidden cost clauses on the
    the same subject must not change it (not by being counted, and not by ranking)."""
    accounts, rfq_id = stream_api
    plain = write_count(accounts, rfq_id)
    first = pdf_from_html(tmp_path / "01_rfq.pdf", PACKAGE_MAIN + HIDDEN_EXTRA)
    other = make_package(tmp_path / "base")[1]
    richer = accounts.upload([first, other])
    assert richer != rfq_id
    assert plain > 0
    assert write_count(accounts, richer, FULL_VIEW) > write_count(accounts, rfq_id, FULL_VIEW), (
        "the extra clauses are real and findable by an account that may see them"
    )
    assert write_count(accounts, richer) == plain


def test_the_log_of_a_failing_stream_holds_the_exception_type_only(
    stream_api: Api, caplog: pytest.LogCaptureFixture
):
    accounts, rfq_id = stream_api
    accounts.state.chat_model = Reader(fail=RuntimeError(LEAK))
    with caplog.at_level("DEBUG"):
        open_stream(accounts[FULL_VIEW], rfq_id, {"question": QUESTION})
    text = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert "1.2345" not in text and "provider said" not in text and "prompt text" not in text
    assert not [r for r in caplog.records if r.name.startswith("autorfx") and r.exc_info]
