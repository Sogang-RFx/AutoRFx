"""``POST /rfqs/{rfq_id}/chat/stream``: the answer of ``/chat``, with its progress as server-sent events.

docs/design/immersive-ui.md §5. Everything that can be refused is refused before the response starts, as an
ordinary JSON error. The answer is then made by the same function as the plain route, on a worker thread of
the application's own pool; its stage and progress events pass through a queue, and the stream ends with
exactly one ``done`` or ``error``. No word of a draft is sent before it is verified (CITE-01).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Executor, Future
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from autorfx.api.bounded import json_body
from autorfx.api.chat_context import ChatBody, chat_context
from autorfx.api.routes import envelope
from autorfx.api.scoping import CurrentRfq
from autorfx.api.security import State, current_viewer
from autorfx.api.state import ApiError
from autorfx.chat.service import answer_question, clean_question

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(current_viewer)])
HEARTBEAT_SECONDS = 10.0
"""A comment line this often while nothing happens, so that a proxy does not close an idle connection."""
HEADERS = {"X-Accel-Buffering": "no"}
"""Every response already carries ``Cache-Control: no-store`` (``ResponseHeaders``), which suits a stream."""
MAX_BODY_BYTES = 128 * 1024
"""The cap counts bytes and ``ChatBody`` limits characters: a question of 500 and six turns of 500 + 4,000
are 27,500 characters, up to 4 bytes each in UTF-8 (Korean takes 3), so 110,000 bytes plus the JSON around
them. A smaller cap would refuse a long Korean conversation that the contract accepts."""
FAILED = {
    "type": "error",
    "error": {"code": "chat_failed", "message": "답변을 만들지 못했습니다. 잠시 뒤에 다시 시도해 주세요."},
}
"""Safe for the browser: no exception name, no prompt, no provider text."""

Work = Callable[[Callable[[dict], None]], dict]
"""Makes the answer, reporting progress to the function it is given; returns the ``data`` of the reply."""
Frame = tuple[str, bool]
"""One serialised event and whether it is the last."""


@router.post("/rfqs/{rfq_id}/chat/stream")
def chat_stream(
    rfq: CurrentRfq,
    body: Annotated[ChatBody, Depends(json_body(ChatBody, limit=MAX_BODY_BYTES))],
    state: State,
) -> StreamingResponse:
    try:
        question = clean_question(body.question)
    except ValueError as exc:
        raise ApiError(422, "invalid_request", str(exc)) from exc
    context, scope, model, history = chat_context(rfq, state), rfq.scope, state.chat_model, body.turns()

    def work(observer: Callable[[dict], None]) -> dict:
        return answer_question(
            question, context=context, scope=scope, model=model, history=history, observer=observer
        )

    return StreamingResponse(
        event_stream(work, state.stream_executor), media_type="text/event-stream", headers=HEADERS
    )


def encode(event: dict) -> str:
    """One server-sent event. ``NaN`` and ``Infinity`` are not JSON: a browser could not read them."""
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return f"data: {payload}\n\n"


async def event_stream(work: Work, executor: Executor) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Frame] = asyncio.Queue()
    gone = threading.Event()
    lock = threading.Lock()
    closed = False

    def post(text: str, last: bool) -> None:
        nonlocal closed
        with lock:
            if closed or gone.is_set():
                return
            closed = closed or last  # whatever comes after the last event is dropped
        try:
            loop.call_soon_threadsafe(queue.put_nowait, (text, last))
        except RuntimeError:  # the loop is closed: nobody is listening any more
            gone.set()

    def progress(event: dict) -> None:
        try:
            post(encode(event), False)
        except ValueError:  # an event that is not JSON is lost; the answer is not
            log.warning("chat stream event dropped (not JSON)")

    def run() -> None:
        if gone.is_set():  # the client left while this waited for a thread: no model call for nobody
            return
        try:
            post(encode({"type": "done", "response": envelope(work(progress), chat="real")}), True)
        except BaseException as exc:  # a stream must end whatever was raised
            # The kind only, as in the chat service: a message may quote a prompt, which holds document text.
            log.warning("chat stream failed (%s)", type(exc.__cause__ or exc).__name__)
        finally:
            post(encode(FAILED), True)  # dropped when a done event has gone first

    def cancelled(future: Future) -> None:
        if future.cancelled():  # the pool shut down with this answer still waiting
            post(encode(FAILED), True)

    try:
        executor.submit(run).add_done_callback(cancelled)
    except RuntimeError:  # the pool refuses new work: the application is stopping
        log.warning("chat stream refused by the worker pool")
        yield encode(FAILED)
        return
    try:
        while True:
            try:
                async with asyncio.timeout(HEARTBEAT_SECONDS):
                    text, last = await queue.get()
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield text
            if last:
                return
    finally:
        gone.set()
