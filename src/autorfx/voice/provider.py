"""The seam to the speech provider (docs/design/immersive-ui.md §6.1) and its test double."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar

T = TypeVar("T")


class SpeechProvider(Protocol):
    """Both calls raise ``TransientProviderError`` when the provider cannot answer right now."""

    id: str

    def transcribe(self, audio: bytes, *, mime: str, hint: str) -> str:
        """The words of a recording. ``hint`` is a list of domain words, never text of a document."""
        ...

    def speak(self, text: str) -> bytes:
        """``text`` read aloud, as MP3."""
        ...


class ScriptedSpeech:
    """Test double: replays queued results (a value is returned, an exception is raised) and records calls."""

    id = "scripted"

    def __init__(
        self, transcripts: Sequence[str | Exception] = (), speeches: Sequence[bytes | Exception] = ()
    ) -> None:
        self._transcripts = list(transcripts)
        self._speeches = list(speeches)
        self.transcribe_calls: list[tuple[bytes, str, str]] = []
        self.speak_calls: list[str] = []

    def transcribe(self, audio: bytes, *, mime: str, hint: str) -> str:
        self.transcribe_calls.append((audio, mime, hint))
        return _replay(self._transcripts)

    def speak(self, text: str) -> bytes:
        self.speak_calls.append(text)
        return _replay(self._speeches)


def _replay(queue: list[T | Exception]) -> T:
    if not queue:
        raise RuntimeError("ScriptedSpeech has nothing left to replay")
    item = queue.pop(0)
    if isinstance(item, Exception):
        raise item
    return item
