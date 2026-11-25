"""Speech to text and text to speech with the OpenAI audio API (docs/design/immersive-ui.md §6).

The ``openai`` package is imported when a call is made, so building the provider needs no network and no
package. Every failure is one kind of failure to the caller; its words may hold a key or a prompt, and are
never passed on.
"""

from __future__ import annotations

import logging
from functools import cached_property
from typing import TYPE_CHECKING

from autorfx.analysis.providers import TransientProviderError
from autorfx.config import Settings
from autorfx.voice.limits import FILE_SUFFIX
from autorfx.voice.provider import SpeechProvider

if TYPE_CHECKING:
    from openai import OpenAI

log = logging.getLogger(__name__)

TRANSCRIBE_TIMEOUT = 30.0
SPEAK_TIMEOUT = 30.0
MAX_RETRIES = 1
SPEECH_FORMAT = "mp3"
DEFAULT_SUFFIX = "webm"


class OpenAISpeech:
    def __init__(
        self, *, api_key: str, stt_model: str, tts_model: str, voice: str, http_client: object | None = None
    ) -> None:
        self.id = f"openai:{stt_model}+{tts_model}"
        self._key = api_key
        self._stt_model = stt_model
        self._tts_model = tts_model
        self._voice = voice
        self._http_client = http_client  # tests give it a transport that never reaches the network

    def __repr__(self) -> str:
        return f"OpenAISpeech({self.id!r}, voice={self._voice!r})"

    @cached_property
    def _client(self) -> OpenAI:
        from openai import OpenAI

        return OpenAI(api_key=self._key, max_retries=MAX_RETRIES, http_client=self._http_client)  # type: ignore[arg-type]

    def transcribe(self, audio: bytes, *, mime: str, hint: str) -> str:
        name = f"question.{FILE_SUFFIX.get(mime, DEFAULT_SUFFIX)}"
        options = {"prompt": hint} if hint else {}
        try:
            reply = self._client.audio.transcriptions.create(
                model=self._stt_model,
                file=(name, audio, mime),
                timeout=TRANSCRIBE_TIMEOUT,
                **options,  # type: ignore[arg-type]
            )
            return reply.text.strip()
        except Exception as exc:  # any client or network failure is one kind of failure to the caller
            log.warning("transcription failed (%s)", type(exc).__name__)
            raise TransientProviderError(type(exc).__name__) from exc

    def speak(self, text: str) -> bytes:
        try:
            reply = self._client.audio.speech.create(
                model=self._tts_model,
                voice=self._voice,  # type: ignore[arg-type]
                input=text,
                response_format=SPEECH_FORMAT,
                timeout=SPEAK_TIMEOUT,
            )
            sound = reply.content
        except Exception as exc:
            log.warning("speech failed (%s)", type(exc).__name__)
            raise TransientProviderError(type(exc).__name__) from exc
        if not sound:
            raise TransientProviderError("no audio")
        return sound


def make_speech(settings: Settings) -> SpeechProvider | None:
    """The provider when a key is configured and voice is not switched off, otherwise none (ENGINE-03)."""
    if settings.openai_api_key is None or not settings.voice_enabled:
        return None
    return OpenAISpeech(
        api_key=settings.openai_api_key.get_secret_value(),
        stt_model=settings.openai_stt_model,
        tts_model=settings.openai_tts_model,
        voice=settings.openai_tts_voice,
    )
