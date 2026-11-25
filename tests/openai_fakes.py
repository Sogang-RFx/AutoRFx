"""A fake OpenAI server for tests: an ``httpx`` mock transport that speaks the chat and audio wire formats.

The real client libraries run on top of it, so what is tested is our code in the shape the libraries give it
(the streamed events, the exceptions), with no key and no network. All text here is invented.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

CHUNK = {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1, "model": "gpt-test"}
PIECE = 8
"""Characters per streamed piece: small, so that a document arrives in many events."""

Handler = Callable[[httpx.Request], httpx.Response]


def _event(delta: dict, finish: str | None = None) -> dict:
    return {**CHUNK, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def chat_stream_body(document: str, *, finish: str = "stop", piece: int = PIECE) -> bytes:
    """A streamed chat completion whose message is ``document``, cut into pieces of ``piece`` characters."""
    events = [_event({"role": "assistant", "content": ""})]
    events += [_event({"content": document[i : i + piece]}) for i in range(0, len(document), piece)]
    events.append(_event({}, finish))
    return ("".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n").encode()


def refusal_stream_body(words: str = "I cannot help with that.") -> bytes:
    events = [_event({"role": "assistant", "content": ""}), _event({"refusal": words}), _event({}, "stop")]
    return ("".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n").encode()


def stream_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def error_response(status: int, message: str = "provider words") -> httpx.Response:
    """An API error. ``retry-after-ms`` keeps the SDK's one retry from sleeping."""
    return httpx.Response(
        status,
        headers={"content-type": "application/json", "retry-after-ms": "1"},
        content=json.dumps({"error": {"message": message, "type": "test"}}).encode(),
    )


class FakeServer:
    """Counts the requests it gets and answers each with ``handler``."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler
        self.client = httpx.Client(transport=httpx.MockTransport(self._answer))

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)
