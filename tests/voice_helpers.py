"""Shared by the voice tests: an app with a speech provider injected, and the uploads the routes take.

``autorfx.voice`` is imported inside the functions: while it is missing, the tests that need it fail one by
one and every other test file still runs.

Names that docs/design/immersive-ui.md §6.1 leaves open are fixed here, and the backend follows them:

- ``ScriptedSpeech(transcripts=(), speeches=())``: queues like ``ScriptedChatModel``. Each ``transcribe``
  takes the next item of ``transcripts`` (a ``str`` is returned, an ``Exception`` is raised), each ``speak``
  the next of ``speeches`` (``bytes``, or an ``Exception``). ``id`` is ``"scripted"``.
- ``.transcribe_calls``: a list of ``(audio, mime, hint)``; ``.speak_calls``: a list of ``text``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib import import_module

from auth_helpers import Accounts, create_accounts, make_client
from autorfx.analysis import RuleOnlyProvider
from autorfx.api import create_app
from autorfx.config import Settings
from autorfx.rules import Ruleset

MP3 = b"ID3\x04\x00scripted-mp3-bytes\xff\xfb\x90"
SPOKEN = "납기와 인도조건을 알려 주세요."
AUDIO = b"RIFF\x00\x00\x00\x00WAVEfmt scripted-recording"
MAX_AUDIO_BYTES = 2 * 1024 * 1024
MAX_TRANSCRIPT_CHARS = 2000
MAX_JSON_BYTES = 64 * 1024
MAX_SPEECH_CHARS = 1200
MAX_SECONDS = 60
SOUND_TYPES = ("audio/webm", "audio/mp4", "audio/mpeg", "audio/wav", "audio/ogg")
LEAK = "401 Unauthorized from api.openai.com: key sk-test-leak-1234 for org-secret"


def scripted_speech(  # type: ignore[no-untyped-def]
    transcripts: Sequence[str | Exception] = (), speeches: Sequence[bytes | Exception] = ()
):
    return import_module("autorfx.voice").ScriptedSpeech(
        transcripts=list(transcripts), speeches=list(speeches)
    )


@contextmanager
def voice_app(
    settings: Settings, ruleset: Ruleset, *, speech: object | None = None, names: Sequence[str] = ("sales",)
) -> Iterator[Accounts]:
    """``running_app`` with ``create_app(..., speech=...)``, which ``running_app`` cannot pass on."""
    create_accounts(settings, ruleset, names)
    app = create_app(settings, ruleset=ruleset, provider=RuleOnlyProvider(), speech=speech)  # type: ignore[arg-type]
    with make_client(app):
        yield Accounts(app, settings)


def audio_part(
    content: bytes = AUDIO, mime: str = "audio/webm", name: str = "question.webm"
) -> dict[str, tuple[str, bytes, str]]:
    return {"audio": (name, content, mime)}


BOUNDARY = "autorfx-test-boundary"
CHUNK = 16 * 1024


def multipart_chunks(content: bytes, mime: str = "audio/webm") -> list[bytes]:
    """The body of an upload cut into pieces, to be sent without a ``Content-Length`` (chunked)."""
    head = (
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="audio"; filename="q.webm"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    body = head + content + f"\r\n--{BOUNDARY}--\r\n".encode()
    return [body[i : i + CHUNK] for i in range(0, len(body), CHUNK)]


MULTIPART_TYPE = f"multipart/form-data; boundary={BOUNDARY}"
