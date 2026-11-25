/** The voice state machine and the plain-Korean errors. */
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/rfq/provider";
import { VoiceUnsupportedError, voiceErrorOf, voiceMessage } from "@/lib/voice/errors";
import {
  IDLE,
  MIN_RECORDING_MS,
  voiceReducer,
  type VoiceEvent,
  type VoiceState,
} from "@/lib/voice/state";

const run = (events: VoiceEvent[], from: VoiceState = IDLE): VoiceState => events.reduce(voiceReducer, from);
const phases = (events: VoiceEvent[]) => {
  const seen: string[] = [];
  events.reduce((state, e) => {
    const next = voiceReducer(state, e);
    seen.push(next.phase);
    return next;
  }, IDLE);
  return seen;
};
const stopped = (elapsedMs: number): VoiceEvent => ({ type: "recording_stopped", elapsedMs });

describe("the way of one spoken question", () => {
  it("goes idle → permission → recording → transcribing → idle", () => {
    expect(
      phases([
        { type: "mic_requested" },
        { type: "recording_started" },
        stopped(2500),
        { type: "transcribed" },
      ]),
    ).toEqual(["asking_permission", "recording", "transcribing", "idle"]);
  });

  it("goes idle → speaking → idle for a read answer", () => {
    expect(phases([{ type: "speech_started" }, { type: "speech_ended" }])).toEqual(["speaking", "idle"]);
  });

  it("stops the playback when the microphone is pressed", () => {
    const speaking = run([{ type: "speech_started" }]);
    expect(voiceReducer(speaking, { type: "mic_requested" }).phase).toBe("asking_permission");
  });

  it("rejects a recording that is too short, and keeps the reason", () => {
    const state = run([{ type: "mic_requested" }, { type: "recording_started" }, stopped(MIN_RECORDING_MS - 1)]);
    expect(state).toEqual({ phase: "error", error: "too_short" });
    expect(run([{ type: "mic_requested" }, { type: "recording_started" }, stopped(MIN_RECORDING_MS)]).phase).toBe(
      "transcribing",
    );
  });
});

describe("what is ignored", () => {
  it("does not start a second recording while one is under way", () => {
    for (const phase of ["asking_permission", "recording", "transcribing"] as const) {
      expect(voiceReducer({ phase, error: null }, { type: "mic_requested" }).phase).toBe(phase);
    }
  });

  it("does not read an answer while recording or transcribing", () => {
    for (const phase of ["asking_permission", "recording", "transcribing"] as const) {
      expect(voiceReducer({ phase, error: null }, { type: "speech_started" }).phase).toBe(phase);
    }
  });

  it("ignores events that do not fit the phase", () => {
    expect(voiceReducer(IDLE, { type: "recording_started" })).toBe(IDLE);
    expect(voiceReducer(IDLE, stopped(5000))).toBe(IDLE);
    expect(voiceReducer(IDLE, { type: "transcribed" })).toBe(IDLE);
    expect(voiceReducer(IDLE, { type: "speech_ended" })).toBe(IDLE);
  });
});

describe("cancelling and failing", () => {
  it("cancels permission, recording and transcribing back to idle", () => {
    for (const phase of ["asking_permission", "recording", "transcribing"] as const) {
      expect(voiceReducer({ phase, error: null }, { type: "cancelled" })).toEqual(IDLE);
    }
  });

  it("fails from any phase with the reason, and can start again from the error", () => {
    const failed = run([{ type: "mic_requested" }, { type: "failed", kind: "permission_denied" }]);
    expect(failed).toEqual({ phase: "error", error: "permission_denied" });
    expect(voiceReducer(failed, { type: "mic_requested" }).phase).toBe("asking_permission");
  });

  it("clears the error when something new begins", () => {
    const failed: VoiceState = { phase: "error", error: "unavailable" };
    expect(voiceReducer(failed, { type: "speech_started" })).toEqual({ phase: "speaking", error: null });
  });
});

describe("errors in plain Korean", () => {
  const dom = (name: string) => new DOMException("x", name);
  const api = (status: number, code = "x") => new ApiError("chat", status, code, "서버 문장");

  it("names what happened to the microphone", () => {
    expect(voiceErrorOf(dom("NotAllowedError"), "record")).toBe("permission_denied");
    expect(voiceErrorOf(dom("NotFoundError"), "record")).toBe("no_microphone");
    expect(voiceErrorOf(dom("NotReadableError"), "record")).toBe("mic_busy");
    expect(voiceErrorOf(new Error("x"), "record")).toBe("failed");
    expect(voiceErrorOf(new VoiceUnsupportedError(), "record")).toBe("unsupported");
    expect(voiceErrorOf(dom("NotAllowedError"), "speak")).toBe("playback_blocked");
  });

  it("names what the service answered", () => {
    expect(voiceErrorOf(api(503, "voice_unavailable"), "transcribe")).toBe("unavailable");
    expect(voiceErrorOf(api(0, "network"), "speak")).toBe("unavailable");
    expect(voiceErrorOf(api(413), "transcribe")).toBe("too_long");
    expect(voiceErrorOf(api(422), "transcribe")).toBe("too_long");
    expect(voiceErrorOf(api(413), "speak")).toBe("speech_too_long");
    expect(voiceErrorOf(api(415), "transcribe")).toBe("unsupported");
    expect(voiceErrorOf(api(500), "speak")).toBe("failed");
  });

  it("does not count an abort as an error", () => {
    expect(voiceErrorOf(dom("AbortError"), "speak")).toBeNull();
  });

  it("says what to do next for every kind, politely, without the provider's words", () => {
    const kinds = [
      "permission_denied",
      "no_microphone",
      "mic_busy",
      "unsupported",
      "too_short",
      "too_long",
      "speech_too_long",
      "playback_blocked",
      "no_speech",
      "unavailable",
      "failed",
    ] as const;
    for (const kind of kinds) {
      const message = voiceMessage(kind);
      expect(message, kind).toMatch(/[.]$/);
      expect(message, kind).toMatch(/(니다|세요)\.$/);
      expect(message, kind).not.toMatch(/openai|whisper|undefined/i);
    }
  });
});
