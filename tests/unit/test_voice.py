"""Voice questions and spoken answers: ``/meta``, the two ``/voice`` routes and the provider seam.

docs/design/immersive-ui.md §6, §6.1, §7. The provider is a double: no network, no key. The routes need a
session, refuse what is too big or of the wrong kind before the provider is called, and never pass a
provider's own words on to the browser. No sentence of an RFQ is sent as a recognition hint (INV-8).
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from importlib import import_module

import pytest

from auth_helpers import Accounts, settings_in
from auth_helpers import error_of as error
from autorfx.analysis import TransientProviderError
from autorfx.config import Settings
from autorfx.rules import Ruleset
from contract_schema import assert_valid
from factories import make_package
from voice_helpers import (
    AUDIO,
    LEAK,
    MAX_AUDIO_BYTES,
    MAX_SECONDS,
    MAX_SPEECH_CHARS,
    MP3,
    SOUND_TYPES,
    SPOKEN,
    audio_part,
    scripted_speech,
    voice_app,
)

CLIENT = "sales"
LIMITS = {"max_seconds": MAX_SECONDS, "max_speech_chars": MAX_SPEECH_CHARS}
PACKAGE_FRAGMENTS = ("seat brackets", "drawing AB-100", "10 working days", "36 months", "1.2345")
GONE = "voice_unavailable"
FAILED = "voice_failed"


@pytest.fixture(scope="module")
def keyless(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Accounts]:
    """The app as it runs without an OpenAI key: no speech provider."""
    root = tmp_path_factory.mktemp("voice-keyless")
    with voice_app(settings_in(root), ruleset) as accounts:
        yield accounts


@pytest.fixture(scope="module")
def spoken(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Accounts]:
    """The app with a provider injected, and one RFQ stored so that a leak of its text could happen."""
    root = tmp_path_factory.mktemp("voice-api")
    with voice_app(settings_in(root), ruleset, speech=scripted_speech()) as accounts:
        accounts.upload(make_package(root / "upload"))
        yield accounts


def provide(accounts: Accounts, *, transcripts: tuple = (), speeches: tuple = ()):  # type: ignore[no-untyped-def]
    """A fresh double for the next request; routes read the provider from the state each time."""
    accounts.state.speech = provider = scripted_speech(transcripts, speeches)
    return provider


def transcribe(accounts: Accounts, files: dict | None = None, *, client: str | None = CLIENT):  # type: ignore[no-untyped-def]
    who = accounts[client] if client else accounts.anonymous()
    return who.post("/voice/transcriptions", files=files if files is not None else audio_part())


def speak(accounts: Accounts, body: dict, *, client: str | None = CLIENT):  # type: ignore[no-untyped-def]
    who = accounts[client] if client else accounts.anonymous()
    return who.post("/voice/speech", json=body)


def route_exists(accounts: Accounts) -> None:
    """A refusal means something only where the route exists: every unknown path is a 404 as well."""
    provide(accounts, transcripts=(SPOKEN,))
    assert transcribe(accounts).status_code == 200


# ── /meta ───────────────────────────────────────────────────────────────────
def test_meta_says_voice_is_off_without_a_provider(keyless: Accounts):
    """§6, §7: the web hides the microphone and the read-aloud switch when this is false."""
    body = keyless[CLIENT].get("/meta").json()
    assert_valid("meta", body)
    assert body["data"]["voice"] == {"transcribe": False, "speak": False, **LIMITS}
    assert keyless.state.speech is None


def test_meta_says_voice_is_on_with_a_provider_and_gives_the_limits(spoken: Accounts):
    provider = provide(spoken)
    body = spoken[CLIENT].get("/meta").json()
    assert_valid("meta", body)
    assert body["data"]["voice"] == {"transcribe": True, "speak": True, **LIMITS}
    assert spoken.state.speech is provider


def test_an_injected_provider_is_kept_on_the_app_state(spoken: Accounts):
    """§6.1: ``create_app(..., speech=...)`` puts it in ``AppState.speech``."""
    assert spoken.state.speech is not None


# ── POST /voice/transcriptions ──────────────────────────────────────────────
def test_a_recording_needs_a_session(spoken: Accounts):
    route_exists(spoken)
    assert error(transcribe(spoken, client=None)) == (401, "unauthenticated")


def test_without_a_provider_a_recording_gets_503_voice_unavailable(keyless: Accounts):
    """§6.1: logged in, but nothing to transcribe with."""
    response = transcribe(keyless)
    assert error(response) == (503, GONE)


def test_a_recording_is_turned_into_text_in_an_envelope(spoken: Accounts):
    """§6: the text is the question to send; the provenance says the chat part is real."""
    provider = provide(spoken, transcripts=(SPOKEN,))
    response = transcribe(spoken)
    assert response.status_code == 200, response.text
    body = response.json()
    assert_valid("voice-transcription", body)
    assert body == {"data": {"text": SPOKEN}, "provenance": {"chat": "real"}}
    ((audio, mime, hint),) = provider.transcribe_calls
    assert audio == AUDIO and mime == "audio/webm" and isinstance(hint, str)


@pytest.mark.parametrize("mime", SOUND_TYPES)
def test_every_accepted_sound_type_reaches_the_provider(spoken: Accounts, mime: str):
    """§6.1: webm, mp4, mpeg, wav and ogg."""
    provider = provide(spoken, transcripts=(SPOKEN,))
    assert transcribe(spoken, audio_part(mime=mime, name="q")).status_code == 200
    assert provider.transcribe_calls[0][1] == mime


def test_a_browser_type_with_codecs_is_accepted(spoken: Accounts):
    """§6: ``MediaRecorder`` says ``audio/webm;codecs=opus``; the type is what comes before ``;``."""
    provider = provide(spoken, transcripts=(SPOKEN,))
    response = transcribe(spoken, audio_part(mime="audio/webm;codecs=opus"))
    assert response.status_code == 200, response.text
    assert provider.transcribe_calls[0][1].startswith("audio/webm")


def test_the_hint_has_glossary_words_and_no_sentence_of_the_package(spoken: Accounts, ruleset: Ruleset):
    """§6, INV-8: words kept in the rules help recognition; the text of a document never goes along."""
    provider = provide(spoken, transcripts=(SPOKEN,))
    assert transcribe(spoken).status_code == 200
    hint = provider.transcribe_calls[0][2]
    words = {word for entry in ruleset.glossary for word in (*entry.terms, *entry.expands_to)}
    assert len([word for word in words if word in hint]) >= 5
    assert not [part for part in PACKAGE_FRAGMENTS if part in hint]
    assert len(hint) < 2000, "a hint is a list of words, not a document"


def test_a_recording_over_two_megabytes_is_refused_before_the_provider(spoken: Accounts):
    """§6: 413, and nothing is sent to the provider. 2 MiB is 60 seconds at any speech bit rate."""
    provider = provide(spoken, transcripts=(SPOKEN,))
    response = transcribe(spoken, audio_part(b"\x00" * (MAX_AUDIO_BYTES + 1)))
    assert error(response) == (413, "too_large")
    assert provider.transcribe_calls == []
    assert transcribe(spoken, audio_part(b"\x00" * (MAX_AUDIO_BYTES // 2))).status_code == 200


@pytest.mark.parametrize("mime", ["audio/flac", "text/plain", "application/octet-stream", "video/webm"])
def test_an_unsupported_type_is_415(spoken: Accounts, mime: str):
    provider = provide(spoken, transcripts=(SPOKEN,))
    response = transcribe(spoken, audio_part(mime=mime))
    assert response.status_code == 415, response.text
    assert_valid("error", response.json())
    assert provider.transcribe_calls == []


def test_an_empty_recording_is_422(spoken: Accounts):
    provider = provide(spoken, transcripts=(SPOKEN,))
    assert error(transcribe(spoken, audio_part(b""))) == (422, "invalid_request")
    assert provider.transcribe_calls == []


def test_a_request_without_the_audio_field_is_422(spoken: Accounts):
    provider = provide(spoken, transcripts=(SPOKEN,))
    other = {"sound": ("q.webm", AUDIO, "audio/webm")}
    assert error(transcribe(spoken, other)) == (422, "invalid_request")
    assert provider.transcribe_calls == []


def test_a_provider_failure_is_503_voice_failed_without_its_words(spoken: Accounts):
    """§6.1: the browser learns that it failed, not why: a provider's message may hold a key or a prompt."""
    provide(spoken, transcripts=(TransientProviderError(LEAK),))
    response = transcribe(spoken)
    assert error(response) == (503, FAILED)
    message = response.json()["error"]["message"]
    assert any("가" <= ch <= "힣" for ch in message), "a message for the user, in Korean"
    for part in ("openai", "sk-test", "401", "Unauthorized", "org-secret", "TransientProviderError"):
        assert part.lower() not in response.text.lower()


# ── POST /voice/speech ──────────────────────────────────────────────────────
def test_speech_needs_a_session(spoken: Accounts):
    provide(spoken, speeches=(MP3,))
    assert speak(spoken, {"text": SPOKEN}).status_code == 200
    assert error(speak(spoken, {"text": SPOKEN}, client=None)) == (401, "unauthenticated")


def test_without_a_provider_speech_gets_503_voice_unavailable(keyless: Accounts):
    assert error(speak(keyless, {"text": SPOKEN})) == (503, GONE)


def test_text_is_read_aloud_as_mp3(spoken: Accounts):
    """§6: the verified sentences the screen shows go in, ``audio/mpeg`` comes out."""
    provider = provide(spoken, speeches=(MP3,))
    body = {"text": SPOKEN}
    assert_valid("voice-speech-request", body)
    response = speak(spoken, body)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].split(";")[0] == "audio/mpeg"
    assert response.content == MP3
    assert provider.speak_calls == [SPOKEN]


@pytest.mark.parametrize("length", [1, MAX_SPEECH_CHARS])
def test_the_shortest_and_the_longest_text_are_read(spoken: Accounts, length: int):
    provider = provide(spoken, speeches=(MP3,))
    assert speak(spoken, {"text": "가" * length}).status_code == 200
    assert provider.speak_calls == ["가" * length]


@pytest.mark.parametrize(
    "body",
    [{"text": ""}, {"text": "가" * (MAX_SPEECH_CHARS + 1)}, {}, {"text": 1}, {"text": SPOKEN, "voice": "x"}],
    ids=["empty", "too-long", "missing", "number", "extra"],
)
def test_text_outside_the_limits_is_422_and_nothing_is_sent(spoken: Accounts, body: dict):
    """§6: 1 to 1,200 characters; the provider is not asked to read more."""
    provider = provide(spoken, speeches=(MP3,))
    assert error(speak(spoken, body)) == (422, "invalid_request")
    assert provider.speak_calls == []


def test_a_speech_failure_is_503_voice_failed_without_the_providers_words(spoken: Accounts):
    provide(spoken, speeches=(TransientProviderError(LEAK),))
    response = speak(spoken, {"text": SPOKEN})
    assert error(response) == (503, FAILED)
    for part in ("openai", "sk-test", "401", "Unauthorized", "org-secret"):
        assert part.lower() not in response.text.lower()


# ── the provider seam (§6.1) ────────────────────────────────────────────────
def test_the_double_records_its_calls_and_replays_its_queues():
    provider = scripted_speech(("첫째", TransientProviderError("down")), (MP3, RuntimeError("no sound")))
    assert provider.id == "scripted"
    assert provider.transcribe(AUDIO, mime="audio/webm", hint="납기, 보증") == "첫째"
    with pytest.raises(TransientProviderError):
        provider.transcribe(b"x", mime="audio/wav", hint="")
    assert provider.speak("안녕하세요") == MP3
    with pytest.raises(RuntimeError):
        provider.speak("다시")
    assert provider.transcribe_calls == [(AUDIO, "audio/webm", "납기, 보증"), (b"x", "audio/wav", "")]
    assert provider.speak_calls == ["안녕하세요", "다시"]


def keyed(**change: object) -> Settings:
    return Settings(_env_file=None, OPENAI_API_KEY="sk-test-not-real", **change)  # type: ignore[call-arg]


def make(settings: Settings):  # type: ignore[no-untyped-def]
    return import_module("autorfx.voice").make_speech(settings)


def test_there_is_no_provider_without_a_key(monkeypatch: pytest.MonkeyPatch):
    """§6, ENGINE-03: no key, no voice."""
    monkeypatch.delenv("VOICE_ENABLED", raising=False)
    assert make(Settings(_env_file=None, OPENAI_API_KEY="")) is None  # type: ignore[call-arg]


def test_a_key_does_not_turn_voice_on_when_it_is_switched_off(monkeypatch: pytest.MonkeyPatch):
    """OQ-FX-2: the team can keep the audio at home while the chat uses the key."""
    monkeypatch.delenv("VOICE_ENABLED", raising=False)
    assert make(keyed(VOICE_ENABLED=False)) is None
    monkeypatch.setenv("VOICE_ENABLED", "false")
    assert make(keyed()) is None


def test_a_key_gives_a_provider_and_building_it_does_not_touch_the_network(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VOICE_ENABLED", raising=False)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the network was touched while building the provider")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    provider = make(keyed(VOICE_ENABLED=True))
    assert provider is not None and isinstance(provider.id, str) and provider.id
    assert callable(provider.transcribe) and callable(provider.speak)
    assert "sk-test-not-real" not in repr(provider) + provider.id
