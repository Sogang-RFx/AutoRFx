"""The worker side of ``/chat/stream``: its own thread pool, and a stream that always ends (§5.2).

The stream generator is driven directly with real executors; no HTTP, no network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest

from auth_helpers import running_app, settings_in
from autorfx.api import chat_stream
from autorfx.api.chat_stream import FAILED, event_stream
from autorfx.config import Settings
from autorfx.rules import Ruleset
from chat_stream_helpers import parse_sse

Work = Callable[[Callable[[dict], None]], dict]
DATA = {"mode": "extractive", "answer": "ok"}


def collect(work: Work, executor: ThreadPoolExecutor, *, limit: int = 20) -> list[dict]:
    async def run() -> str:
        parts: list[str] = []
        async for part in event_stream(work, executor):
            parts.append(part)
            assert len(parts) <= limit
        return "".join(parts)

    return parse_sse(asyncio.run(run()))


@pytest.fixture
def pool():  # type: ignore[no-untyped-def]
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-stream")
    yield executor
    executor.shutdown(wait=True, cancel_futures=True)


def terminal(events: list[dict]) -> list[str]:
    return [e["type"] for e in events if e["type"] in ("done", "error")]


# ── 4. a pool of its own ────────────────────────────────────────────────────
def test_the_pool_size_is_a_setting_that_defaults_to_eight():
    assert Settings(_env_file=None).chat_stream_workers == 8  # type: ignore[call-arg]
    assert Settings(_env_file=None, AUTORFX_CHAT_STREAM_WORKERS="3").chat_stream_workers == 3  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(_env_file=None, AUTORFX_CHAT_STREAM_WORKERS="0")  # type: ignore[call-arg]


def test_the_application_owns_a_bounded_pool_and_shuts_it_down(tmp_path, ruleset: Ruleset):  # type: ignore[no-untyped-def]
    settings = settings_in(tmp_path, AUTORFX_CHAT_STREAM_WORKERS="3")
    with running_app(settings, ruleset) as accounts:
        pool = accounts.state.stream_executor
        assert pool._max_workers == 3
        assert pool.submit(lambda: threading.current_thread().name).result().startswith("autorfx-chat-stream")
    with pytest.raises(RuntimeError):
        pool.submit(lambda: None)


def test_an_answer_is_made_on_the_given_pool_and_not_on_the_shared_one(pool: ThreadPoolExecutor):
    names: list[str] = []

    def work(observer: Callable[[dict], None]) -> dict:
        names.append(threading.current_thread().name)
        return DATA

    assert terminal(collect(work, pool)) == ["done"]
    assert names[0].startswith("test-stream")


def test_a_request_beyond_the_pool_waits_with_heartbeats_and_is_then_answered(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(chat_stream, "HEARTBEAT_SECONDS", 0.05)
    release = threading.Event()
    one = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-one")
    try:
        one.submit(release.wait, 5)  # an abandoned answer holds the only thread
        threading.Timer(0.3, release.set).start()
        raw: list[str] = []

        async def run() -> None:
            async for part in event_stream(lambda observer: DATA, one):
                raw.append(part)

        asyncio.run(run())
    finally:
        release.set()
        one.shutdown(wait=True)
    assert raw[0] == ": keep-alive\n\n" and raw.count(": keep-alive\n\n") >= 2
    assert json.loads(raw[-1][len("data: ") :])["type"] == "done"


def test_an_answer_whose_client_left_before_it_started_is_not_made(pool: ThreadPoolExecutor):
    """Queued behind others, a stream that nobody reads any more must not spend a model call."""
    ran: list[int] = []
    release = threading.Event()
    blockers = [pool.submit(release.wait, 5) for _ in range(2)]

    async def run() -> None:
        stream = event_stream(lambda observer: ran.append(1) or DATA, pool)
        task = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await stream.aclose()

    asyncio.run(run())
    release.set()
    for blocker in blockers:
        blocker.result()
    pool.shutdown(wait=True)
    assert ran == []


# ── 5. a stream always ends ─────────────────────────────────────────────────
def test_a_normal_answer_ends_with_one_done_after_its_progress(pool: ThreadPoolExecutor):
    def work(observer: Callable[[dict], None]) -> dict:
        observer({"type": "stage", "stage": "plan"})
        observer({"type": "progress", "drafted": 1})
        return DATA

    events = collect(work, pool)
    assert [e["type"] for e in events] == ["stage", "progress", "done"]
    assert events[-1]["response"]["data"] == DATA


@pytest.mark.parametrize("bad", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_a_value_json_cannot_carry_ends_the_stream_with_the_safe_error(pool: ThreadPoolExecutor, bad: float):
    """``NaN`` is not JSON; a browser's parser would throw on the very event that ends the stream."""
    events = collect(lambda observer: {"value": bad}, pool)
    assert events == [FAILED]


def test_an_event_that_cannot_be_encoded_does_not_end_the_answer(pool: ThreadPoolExecutor):
    def work(observer: Callable[[dict], None]) -> dict:
        observer({"type": "progress", "drafted": float("nan")})
        return DATA

    assert terminal(collect(work, pool)) == ["done"]


class Stop(BaseException):
    """Not an ``Exception``: what ``except Exception`` lets through."""


@pytest.mark.parametrize("failure", [RuntimeError("x"), Stop(), KeyboardInterrupt(), SystemExit(1)])
def test_whatever_is_raised_exactly_one_terminal_event_comes(
    pool: ThreadPoolExecutor, failure: BaseException
):
    def work(observer: Callable[[dict], None]) -> dict:
        observer({"type": "stage", "stage": "plan"})
        raise failure

    events = collect(work, pool)
    assert events == [{"type": "stage", "stage": "plan"}, FAILED]


def test_an_executor_that_refuses_the_job_gives_one_error():
    closed = ThreadPoolExecutor(max_workers=1)
    closed.shutdown()
    assert collect(lambda observer: DATA, closed) == [FAILED]


def test_a_job_cancelled_by_shutdown_gives_one_error_instead_of_silence():
    release = threading.Event()
    one = ThreadPoolExecutor(max_workers=1)
    one.submit(release.wait, 5)
    threading.Timer(0.2, lambda: (one.shutdown(wait=False, cancel_futures=True), release.set())).start()
    assert collect(lambda observer: DATA, one) == [FAILED]


def test_a_failure_is_logged_by_its_type_only(pool: ThreadPoolExecutor, caplog: pytest.LogCaptureFixture):
    def work(observer: Callable[[dict], None]) -> dict:
        raise RuntimeError("prompt held 1.2345 EUR of the package")

    with caplog.at_level("DEBUG"):
        assert collect(work, pool) == [FAILED]
    text = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert "RuntimeError" in text
    assert "1.2345" not in text and "prompt held" not in text
    assert not [r for r in caplog.records if r.exc_info], "no traceback: it would quote the message"
