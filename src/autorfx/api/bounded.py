"""A request whose body is refused with 413 once it grows past a limit."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from autorfx.api.state import ApiError

M = TypeVar("M", bound=BaseModel)


def bounded(request: Request, limit: int, message: str) -> Request:
    """The same request, whose body is refused with 413 once it grows past ``limit`` bytes.

    ``request.form()`` spools every part before the per-file check can run, so the
    whole body is counted as it arrives; a chunked upload has no length to check first.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ApiError(413, "too_large", message)
    received = 0

    async def receive() -> dict:
        nonlocal received
        event = await request.receive()
        if event["type"] == "http.request":
            received += len(event.get("body", b""))
            if received > limit:
                raise ApiError(413, "too_large", message)
        return event

    return Request(request.scope, receive)


MAX_JSON_BYTES = 64 * 1024
"""The default: enough for 1,200 characters to read aloud at 4 bytes a character, with room to spare. A route
whose body may be longer passes its own ``limit``, worked out in bytes from its limits in characters."""
TOO_BIG_JSON = "요청이 너무 큽니다. 더 짧게 줄여 주세요."


def json_body(model: type[M], *, limit: int = MAX_JSON_BYTES) -> Callable[[Request], Awaitable[M]]:
    """A dependency that reads the JSON body as ``model``, refusing with 413 past ``limit`` bytes.

    The body is counted as it arrives, so a chunked upload with no length is stopped too. A body that does not
    fit the model is refused as FastAPI refuses one: ``RequestValidationError`` with ``body`` in the location.
    """

    async def read(request: Request) -> M:
        raw = await bounded(request, limit, TOO_BIG_JSON).body()
        try:
            return model.model_validate_json(raw)
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_context=False, include_input=False)
            raise RequestValidationError([{**e, "loc": ("body", *e["loc"])} for e in errors]) from exc

    return read
