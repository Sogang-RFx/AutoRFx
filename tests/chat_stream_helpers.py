"""Shared by the chat stream tests: the paths of immersive-ui.md §5.1, one run helper, an SSE reader.

Only the stream tests import this module. ``autorfx.chat.progress`` is imported inside functions, so
while it is missing the tests that need it fail one by one and every other test still runs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from importlib import import_module

from pydantic import BaseModel

from autorfx.analysis import TransientProviderError
from autorfx.chat.model import ScriptedChatModel
from autorfx.chat.models import ChatContext, Draft, Evidence, Plan, Turn
from autorfx.chat.service import answer_question
from chat_helpers import (
    REPLACEMENT_S,
    WARRANTY_S,
    context_for_scope,
    draft,
    scope_for_group,
)
from contract_schema import assert_valid
from factories import make_locus

QUESTION = "보증 기간은?"
GOOD = ("보증 기간은 36개월입니다.", (WARRANTY_S,))
FREE = ("불량 부품은 무상으로 교체합니다.", (REPLACEMENT_S,))
BAD = ("보증 기간은 24개월입니다.", (WARRANTY_S,))
DOWN = TransientProviderError("timeout")
STAGES = ("plan", "search", "write", "verify", "rewrite")
EVENT_KEYS = {"stage": {"type", "stage", "evidence"}, "progress": {"type", "drafted"}}

EVIDENCE = tuple(
    Evidence(number=n, text=text, locus=make_locus(n), file="a.pdf", title="Gewaehr-Titel")
    for n, text in ((1, "Die Frist beträgt 36 Monate."), (2, "Parts are replaced."))
)


def plan(intent: str, question: str = QUESTION, *queries: str) -> Plan:
    return Plan(intent=intent, question=question, queries=queries)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Route:
    """One row of the table in §5.1: a question, who asks, what answers, and the stages that must show."""

    name: str
    question: str
    shape: tuple[str, ...]
    make_model: Callable[[], ScriptedChatModel | None] = lambda: None
    group: str = "cost_visible"
    history: Sequence[Turn] = field(default_factory=tuple)


def scripted(
    *drafts: Draft | Exception, plans: Sequence[Plan | Exception] = ()
) -> Callable[[], ScriptedChatModel]:
    """A factory, because a scripted model is used up by one question."""
    return lambda: ScriptedChatModel(plans=list(plans), drafts=list(drafts))


ASKED_PRICE = "목표가는 얼마인가요?"
FOLLOW_UP = (Turn(question="목표가는?", answer="답할 수 없습니다."),)
GENERATED = ("plan", "search", "write", "progress", "verify")
ROUTES = (
    Route("refused", ASKED_PRICE, ("plan",), group="cost_restricted"),
    Route("refused-with-model", ASKED_PRICE, ("plan",), scripted(), group="cost_restricted"),
    Route(
        "refused-after-planning",
        "그럼 그건 얼마야?",
        ("plan",),
        scripted(plans=[plan("lookup", "목표가는 얼마인가?")]),
        group="cost_restricted",
        history=FOLLOW_UP,
    ),
    Route("guide", "안녕하세요", ("plan",)),
    Route("guide-by-model", "안녕하세요", ("plan",), scripted(plans=[plan("guide", "안녕하세요")])),
    Route("stored", "독소조항을 알려줘", ("plan", "verify")),
    Route(
        "stored-by-model",
        "독소조항을 알려줘",
        ("plan", "verify"),
        scripted(plans=[plan("red_flags", "독소조항을 알려줘")]),
    ),
    Route("extractive", "What is the warranty period?", ("plan", "search", "verify")),
    Route("extractive-nothing", "Who is the CEO?", ("plan", "search")),
    Route("generated", QUESTION, GENERATED, scripted(draft(GOOD, FREE))),
    Route(
        "rewritten",
        QUESTION,
        (*GENERATED, "rewrite", "progress", "verify"),
        scripted(draft(GOOD, BAD), draft(GOOD, FREE)),
    ),
    Route("outage", QUESTION, ("plan", "search", "write", "search", "verify"), scripted(DOWN)),
)
GENERATIVE = tuple(r for r in ROUTES if r.name in ("generated", "rewritten", "outage"))
DRAFT_TEXTS = (GOOD[0], FREE[0], BAD[0])


def run(
    context: ChatContext,
    route: Route,
    *,
    observer: Callable[[dict], None] | None = None,
    model: ScriptedChatModel | None = None,
) -> tuple[dict, ScriptedChatModel | None]:
    """``answer_question`` for one route, with a fresh model. The observer is left out when there is none."""
    scope = scope_for_group(route.group)
    used = model if model is not None else route.make_model()
    options = {} if observer is None else {"observer": observer}
    data = answer_question(
        route.question,
        context=context_for_scope(context, scope),
        scope=scope,
        model=used,
        history=route.history,
        **options,  # type: ignore[arg-type]
    )
    assert_valid("chat", {"data": data, "provenance": {"chat": "real"}})
    return data, used


def watch(context: ChatContext, route: Route) -> tuple[list[dict], dict, ScriptedChatModel | None]:
    """Run a route with a recording observer: every event it got, the reply and the model."""
    events: list[dict] = []
    data, model = run(context, route, observer=events.append)
    for event in events:
        assert_valid("chat-stream-event", event)
    return events, data, model


def shape(events: Sequence[dict]) -> tuple[str, ...]:
    """The stage names in order, a run of ``progress`` events counted as one."""
    out: list[str] = []
    for event in events:
        name = event["stage"] if event["type"] == "stage" else event["type"]
        if not (name == "progress" and out and out[-1] == "progress"):
            out.append(name)
    return tuple(out)


def progress_runs(events: Sequence[dict]) -> list[list[int]]:
    """The ``drafted`` values of each draft: a stage event closes the run before it."""
    runs: list[list[int]] = []
    current: list[int] | None = None
    for event in events:
        if event["type"] == "progress":
            if current is None:
                current = []
                runs.append(current)
            current.append(event["drafted"])
        else:
            current = None
    return runs


def count(partial: str) -> int:
    return import_module("autorfx.chat.progress").count_statements(partial)


# ── the wire ────────────────────────────────────────────────────────────────
def parse_sse(raw: str) -> list[dict]:
    """The JSON of every ``data:`` line. Blocks end with a blank line; comment lines (``:``) are skipped."""
    text = raw.replace("\r\n", "\n")
    assert text.endswith("\n\n"), "every event ends with a blank line"
    events: list[dict] = []
    for block in text.split("\n\n"):
        data_lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        assert len(data_lines) <= 1, f"one data line per event: {data_lines}"
        for line in data_lines:
            assert line.startswith("data: "), line
            events.append(json.loads(line[len("data: ") :]))
    return events


# ── fakes of the model calls ────────────────────────────────────────────────
class Call:
    """A plain structured call that returns or raises what it was given, and keeps what it was sent."""

    def __init__(self, reply: BaseModel | Exception) -> None:
        self.reply = reply
        self.seen: list[tuple[str, str, type]] = []

    def __call__(self, system: str, user: str, schema: type) -> BaseModel:
        self.seen.append((system, user, schema))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class StreamCall:
    """A streaming call: hands the document to ``on_token`` in small pieces, then returns the parsed draft."""

    def __init__(
        self, reply: BaseModel | Exception, *, size: int = 5, watch: list[int] | None = None
    ) -> None:
        self.reply, self.size, self.watch = reply, size, watch
        self.seen: list[tuple[str, str, type]] = []
        self.snapshots: list[list[int]] = []

    def __call__(self, system: str, user: str, schema: type, on_token) -> BaseModel:  # type: ignore[no-untyped-def]
        self.seen.append((system, user, schema))
        if isinstance(self.reply, Exception):
            raise self.reply
        document = self.reply.model_dump_json()
        for start in range(0, len(document), self.size):
            on_token(document[start : start + self.size])
            if self.watch is not None:
                self.snapshots.append(list(self.watch))
        return self.reply
