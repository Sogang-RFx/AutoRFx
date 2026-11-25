"""``POST /voice/transcriptions`` and ``POST /voice/speech`` (docs/design/immersive-ui.md §6, §6.1).

Both need a session. Size and kind are checked before the provider is called, and a provider's own words
never reach the browser: they may hold a key or a prompt.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import StringConstraints
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from autorfx.analysis.providers import TransientProviderError
from autorfx.api.bounded import bounded, json_body
from autorfx.api.routes import envelope
from autorfx.api.security import State, current_viewer
from autorfx.api.state import ApiError, AppState
from autorfx.models import Model
from autorfx.voice import MAX_AUDIO_BYTES, MAX_SPEECH_CHARS, MAX_TRANSCRIPT_CHARS, SOUND_TYPES, SpeechProvider

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(current_viewer)])
FIELD = "audio"
MULTIPART_OVERHEAD = 64 * 1024
CHUNK = 256 * 1024
UNAVAILABLE = ("voice_unavailable", "음성 기능을 쓸 수 없습니다. 글로 질문해 주세요.")
FAILED = ("voice_failed", "음성을 처리하지 못했습니다. 잠시 뒤에 다시 시도해 주세요.")
TOO_LARGE = f"녹음이 너무 큽니다. {MAX_AUDIO_BYTES // (1024 * 1024)}MB까지 받습니다."
TOO_LONG = "질문이 너무 깁니다. 더 짧게 질문해 주세요."


class SpeechBody(Model):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_SPEECH_CHARS)]


def _provider(state: AppState) -> SpeechProvider:
    """Read on every request: the provider may be swapped while the server runs (tests do)."""
    if state.speech is None:
        raise ApiError(503, UNAVAILABLE[0], UNAVAILABLE[1])
    return state.speech


def _failed(exc: TransientProviderError) -> ApiError:
    log.warning("voice provider failed (%s)", type(exc.__cause__ or exc).__name__)
    return ApiError(503, FAILED[0], FAILED[1])


def _sound_type(upload: UploadFile) -> str:
    """The type without its parameters: ``MediaRecorder`` says ``audio/webm;codecs=opus``."""
    kind = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if kind not in SOUND_TYPES:
        raise ApiError(415, "unsupported_media_type", "이 형식의 소리는 받지 않습니다. 다시 녹음해 주세요.")
    return kind


async def _read(upload: UploadFile) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(CHUNK):
        size += len(chunk)
        if size > MAX_AUDIO_BYTES:
            raise ApiError(413, "too_large", TOO_LARGE)
        chunks.append(chunk)
    if not size:
        raise ApiError(422, "invalid_request", "녹음된 소리가 없습니다. 다시 녹음해 주세요.")
    return b"".join(chunks)


async def _recording(request: Request) -> tuple[bytes, str]:
    request = bounded(request, MAX_AUDIO_BYTES + MULTIPART_OVERHEAD, TOO_LARGE)
    try:
        form = await request.form(max_files=1, max_fields=4)
    except HTTPException as exc:
        raise ApiError(422, "invalid_request", "요청 형식이 맞지 않습니다: 녹음") from exc
    upload = form.get(FIELD)
    if not isinstance(upload, UploadFile):
        raise ApiError(422, "invalid_request", f"요청 형식이 맞지 않습니다: {FIELD}")
    kind = _sound_type(upload)
    return await _read(upload), kind


@router.post("/voice/transcriptions")
async def transcribe(request: Request, state: State) -> dict:
    provider = _provider(state)
    audio, kind = await _recording(request)
    try:
        text = await run_in_threadpool(provider.transcribe, audio, mime=kind, hint=state.speech_hint)
    except TransientProviderError as exc:
        raise _failed(exc) from exc
    if len(text) > MAX_TRANSCRIPT_CHARS:  # the transcript is not echoed: it may be anything that was said
        raise ApiError(422, "invalid_request", TOO_LONG)
    return envelope({"text": text}, chat="real")


@router.post("/voice/speech")
def speak(body: Annotated[SpeechBody, Depends(json_body(SpeechBody))], state: State) -> Response:
    provider = _provider(state)
    try:
        sound = provider.speak(body.text)
    except TransientProviderError as exc:
        raise _failed(exc) from exc
    return Response(content=sound, media_type="audio/mpeg")
