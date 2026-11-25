"""Limits and switches of the voice feature, and the OpenAI provider through a fake server (§6).

The recording and the transcript are bounded; voice is off unless the team has switched it on; the real
provider class runs on a fake HTTP transport, so what it passes on and what it hides is tested without a key.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import httpx
import pytest

from auth_helpers import Accounts, create_accounts, make_client, settings_in
from auth_helpers import error_of as error
from autorfx.analysis import RuleOnlyProvider, TransientProviderError
from autorfx.api import create_app
from autorfx.config import Settings
from autorfx.rules import Ruleset
from autorfx.voice import OpenAISpeech
from openai_fakes import FakeServer, error_response
from voice_helpers import (
    AUDIO,
    MAX_AUDIO_BYTES,
    MAX_JSON_BYTES,
    MAX_SPEECH_CHARS,
    MAX_TRANSCRIPT_CHARS,
    MP3,
    MULTIPART_TYPE,
    SPOKEN,
    multipart_chunks,
    scripted_speech,
    voice_app,
)

CLIENT = "sales"
LEAK = "Incorrect API key provided: sk-test-leak-1234 for org-secret"


@pytest.fixture(scope="module")
def spoken(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Accounts]:
    root = tmp_path_factory.mktemp("voice-hardening")
    with voice_app(settings_in(root), ruleset, speech=scripted_speech()) as accounts:
        yield accounts


def provide(accounts: Accounts, *, transcripts: tuple = ()):  # type: ignore[no-untyped-def]
    accounts.state.speech = provider = scripted_speech(transcripts, (MP3,))
    return provider


# ── 6. limits ───────────────────────────────────────────────────────────────
def test_the_recording_cap_is_two_mebibytes():
    """Sixty seconds at 279 kbit/s or less: far above any bit rate a browser records speech at."""
    from autorfx.voice import MAX_SECONDS

    assert MAX_AUDIO_BYTES == 2 * 1024 * 1024
    assert MAX_AUDIO_BYTES * 8 / MAX_SECONDS > 256_000, "even raw 16 kHz 16-bit mono speech fits"


def test_a_transcript_up_to_the_limit_is_returned(spoken: Accounts):
    provide(spoken, transcripts=("가" * MAX_TRANSCRIPT_CHARS,))
    response = spoken[CLIENT].post("/voice/transcriptions", files={"audio": ("q.webm", AUDIO, "audio/webm")})
    assert response.status_code == 200 and len(response.json()["data"]["text"]) == MAX_TRANSCRIPT_CHARS


def test_a_longer_transcript_is_refused_with_a_sentence_that_asks_for_a_shorter_question(spoken: Accounts):
    provide(spoken, transcripts=("가" * (MAX_TRANSCRIPT_CHARS + 1),))
    response = spoken[CLIENT].post("/voice/transcriptions", files={"audio": ("q.webm", AUDIO, "audio/webm")})
    assert error(response) == (422, "invalid_request")
    message = response.json()["error"]["message"]
    assert "짧게" in message and "질문" in message
    assert "가가가" not in response.text, "the transcript itself is not echoed"


def test_an_upload_without_a_length_is_counted_and_refused_past_the_cap(spoken: Accounts):
    """No ``Content-Length`` (chunked): the body is counted as it arrives and stopped at the limit."""
    provider = provide(spoken, transcripts=(SPOKEN,))
    chunks = multipart_chunks(b"\x00" * (MAX_AUDIO_BYTES + 1024))
    response = spoken[CLIENT].post(
        "/voice/transcriptions", content=iter(chunks), headers={"content-type": MULTIPART_TYPE}
    )
    assert "content-length" not in response.request.headers, "the test must reach the counting branch"
    assert error(response) == (413, "too_large")
    assert provider.transcribe_calls == []


def test_an_upload_without_a_length_within_the_cap_is_accepted(spoken: Accounts):
    provider = provide(spoken, transcripts=(SPOKEN,))
    response = spoken[CLIENT].post(
        "/voice/transcriptions",
        content=iter(multipart_chunks(AUDIO * 100)),
        headers={"content-type": MULTIPART_TYPE},
    )
    assert response.status_code == 200, response.text
    assert provider.transcribe_calls[0][0] == AUDIO * 100


# ── 8. JSON bodies ──────────────────────────────────────────────────────────
def test_a_speech_body_past_the_cap_is_413_by_its_declared_length(spoken: Accounts):
    provider = provide(spoken)
    response = spoken[CLIENT].post("/voice/speech", json={"text": "가" * MAX_JSON_BYTES})
    assert error(response) == (413, "too_large")
    assert provider.speak_calls == []


def test_a_speech_body_past_the_cap_is_413_without_a_declared_length(spoken: Accounts):
    provider = provide(spoken)
    pieces = [b'{"text": "', b"x" * (MAX_JSON_BYTES + 1), b'"}']
    response = spoken[CLIENT].post(
        "/voice/speech", content=iter(pieces), headers={"content-type": "application/json"}
    )
    assert "content-length" not in response.request.headers, "the test must reach the counting branch"
    assert error(response) == (413, "too_large")
    assert provider.speak_calls == []


def test_a_speech_body_inside_the_cap_is_read_as_before(spoken: Accounts):
    provider = provide(spoken)
    response = spoken[CLIENT].post("/voice/speech", json={"text": "가" * MAX_SPEECH_CHARS})
    assert response.status_code == 200 and provider.speak_calls == ["가" * MAX_SPEECH_CHARS]


@pytest.mark.parametrize("raw", [b"not json", b"[]", b""], ids=["text", "list", "empty"])
def test_a_body_that_is_not_the_object_is_422_as_before(spoken: Accounts, raw: bytes):
    provide(spoken)
    response = spoken[CLIENT].post("/voice/speech", content=raw, headers={"content-type": "application/json"})
    assert error(response) == (422, "invalid_request")


# ── 7. VOICE_ENABLED ────────────────────────────────────────────────────────
def settings_with(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.delenv("VOICE_ENABLED", raising=False)
    return Settings(_env_file=None, OPENAI_API_KEY="sk-test-not-real", **env)  # type: ignore[call-arg]


def test_voice_is_off_by_default(monkeypatch: pytest.MonkeyPatch):
    from autorfx.voice import make_speech

    settings = settings_with(monkeypatch)
    assert settings.voice_enabled is False and make_speech(settings) is None


@pytest.mark.parametrize("blank", ["", "  "])
def test_a_blank_switch_is_off_and_does_not_stop_the_server(monkeypatch: pytest.MonkeyPatch, blank: str):
    """``.env.example`` leaves variables empty; an empty ``VOICE_ENABLED=`` is not an error."""
    monkeypatch.setenv("VOICE_ENABLED", blank)
    assert Settings(_env_file=None).voice_enabled is False  # type: ignore[call-arg]


def test_voice_is_on_only_with_the_switch_and_a_key(monkeypatch: pytest.MonkeyPatch):
    from autorfx.voice import make_speech

    assert make_speech(settings_with(monkeypatch, VOICE_ENABLED="true")) is not None
    monkeypatch.delenv("VOICE_ENABLED", raising=False)
    assert make_speech(Settings(_env_file=None, VOICE_ENABLED="true")) is None  # type: ignore[call-arg]


def configured_app(root, ruleset: Ruleset, monkeypatch: pytest.MonkeyPatch, **env: str):  # type: ignore[no-untyped-def]
    """``create_app`` as a deployment builds it: a key, no injected provider; only the provider and the
    chat model are replaced so that nothing is reached over the network."""
    monkeypatch.setattr("autorfx.api.state.make_provider", lambda settings, rules: RuleOnlyProvider())
    monkeypatch.setattr("autorfx.api.state.make_chat_model", lambda settings: None)
    monkeypatch.setattr("autorfx.api.state.load_prices", lambda path: None)
    settings = settings_in(root, OPENAI_API_KEY="sk-test-not-real", EMBEDDING_MODEL="", **env)
    create_accounts(settings, ruleset, (CLIENT,))
    return create_app(settings, ruleset=ruleset)


@pytest.mark.parametrize(
    ("env", "expected"),
    [({"VOICE_ENABLED": "false"}, False), ({}, False), ({"VOICE_ENABLED": "true"}, True)],
    ids=["switched-off", "default", "switched-on"],
)
def test_meta_follows_the_switch_when_the_app_is_built_with_a_key(
    tmp_path,
    ruleset: Ruleset,
    monkeypatch: pytest.MonkeyPatch,
    env: dict,
    expected: bool,  # type: ignore[no-untyped-def]
):
    monkeypatch.delenv("VOICE_ENABLED", raising=False)
    app = configured_app(tmp_path, ruleset, monkeypatch, **env)
    with make_client(app):
        accounts = Accounts(app, settings_in(tmp_path))
        voice = accounts[CLIENT].get("/meta").json()["data"]["voice"]
        assert (voice["transcribe"], voice["speak"]) == (expected, expected)
        assert (accounts.state.speech is not None) is expected


# ── 9. the OpenAI provider on a fake server ─────────────────────────────────
def provider_on(server: FakeServer) -> OpenAISpeech:
    return OpenAISpeech(
        api_key="sk-test-not-real",
        stt_model="stt-test",
        tts_model="tts-test",
        voice="alloy",
        http_client=server.client,  # type: ignore[call-arg]
    )


def transcription_server(text: str = "  납기는 언제입니까?  ") -> FakeServer:
    return FakeServer(lambda request: httpx.Response(200, json={"text": text}))


def test_a_transcription_passes_the_type_the_name_and_the_hint_on_and_trims_the_text():
    server = transcription_server()
    text = provider_on(server).transcribe(AUDIO, mime="audio/wav", hint="납기, 보증")
    assert text == "납기는 언제입니까?"
    (request,) = server.requests
    body = request.read()
    assert request.url.path.endswith("/audio/transcriptions")
    assert b'filename="question.wav"' in body and b"Content-Type: audio/wav" in body
    assert AUDIO in body and b"stt-test" in body
    assert "납기, 보증".encode() in body and b'name="prompt"' in body


def test_a_blank_hint_sends_no_prompt_and_an_unknown_type_falls_back_to_webm():
    server = transcription_server()
    provider_on(server).transcribe(AUDIO, mime="audio/x-unknown", hint="")
    body = server.requests[0].read()
    assert b'name="prompt"' not in body and b'filename="question.webm"' in body


def test_speech_asks_for_mp3_with_the_text_the_model_and_the_voice():
    server = FakeServer(
        lambda request: httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    )
    assert provider_on(server).speak(SPOKEN) == MP3
    (request,) = server.requests
    sent = json.loads(request.read())
    assert request.url.path.endswith("/audio/speech")
    assert sent["input"] == SPOKEN and sent["model"] == "tts-test" and sent["voice"] == "alloy"
    assert sent["response_format"] == "mp3"


@pytest.mark.parametrize("status", [401, 429, 500])
def test_a_failure_of_either_call_is_one_kind_of_failure_without_the_providers_words(
    status: int, caplog: pytest.LogCaptureFixture
):
    server = FakeServer(lambda request: error_response(status, LEAK))
    provider = provider_on(server)
    with caplog.at_level(logging.DEBUG):
        for call in (
            lambda: provider.transcribe(AUDIO, mime="audio/webm", hint="x"),
            lambda: provider.speak(SPOKEN),
        ):
            with pytest.raises(TransientProviderError) as raised:
                call()
            assert str(raised.value).endswith("Error") and "sk-test" not in str(raised.value)
            assert "org-secret" not in str(raised.value) and "Incorrect" not in str(raised.value)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "org-secret" not in logged and "sk-test-leak" not in logged and "Incorrect" not in logged


def test_a_reply_without_sound_is_a_failure():
    server = FakeServer(
        lambda request: httpx.Response(200, content=b"", headers={"content-type": "audio/mpeg"})
    )
    with pytest.raises(TransientProviderError):
        provider_on(server).speak(SPOKEN)
