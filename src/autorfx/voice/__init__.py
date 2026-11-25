"""Voice questions and spoken answers (docs/design/immersive-ui.md §6)."""

from __future__ import annotations

from autorfx.voice.hints import recognition_hint
from autorfx.voice.limits import (
    MAX_AUDIO_BYTES,
    MAX_SECONDS,
    MAX_SPEECH_CHARS,
    MAX_TRANSCRIPT_CHARS,
    SOUND_TYPES,
)
from autorfx.voice.openai_speech import OpenAISpeech, make_speech
from autorfx.voice.provider import ScriptedSpeech, SpeechProvider

__all__ = [
    "MAX_AUDIO_BYTES",
    "MAX_SECONDS",
    "MAX_SPEECH_CHARS",
    "MAX_TRANSCRIPT_CHARS",
    "SOUND_TYPES",
    "OpenAISpeech",
    "ScriptedSpeech",
    "SpeechProvider",
    "make_speech",
    "recognition_hint",
    "voice_meta",
]


def voice_meta(speech: SpeechProvider | None) -> dict:
    """The ``voice`` object of ``/meta``: the web hides the microphone and the read-aloud switch when off."""
    on = speech is not None
    return {
        "transcribe": on,
        "speak": on,
        "max_seconds": MAX_SECONDS,
        "max_speech_chars": MAX_SPEECH_CHARS,
    }
