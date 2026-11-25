/**
 * The phases of the voice feature as a pure state machine (docs/design/immersive-ui.md §6): idle, asking for
 * the microphone, recording, transcribing, speaking, error. The hook feeds it facts (the recorder started,
 * the answer arrived); it decides what they mean, so the rules can be tested without a browser.
 */
export type VoicePhase = "idle" | "asking_permission" | "recording" | "transcribing" | "speaking" | "error";

export type VoiceErrorKind =
  | "permission_denied"
  | "no_microphone"
  | "mic_busy"
  | "unsupported"
  | "too_short"
  | "too_long"
  | "speech_too_long"
  | "playback_blocked"
  | "no_speech"
  | "unavailable"
  | "failed";

export interface VoiceState {
  phase: VoicePhase;
  error: VoiceErrorKind | null;
}

export type VoiceEvent =
  | { type: "mic_requested" }
  | { type: "recording_started" }
  | { type: "recording_stopped"; elapsedMs: number }
  | { type: "transcribed" }
  | { type: "speech_started" }
  | { type: "speech_ended" }
  | { type: "cancelled" }
  | { type: "failed"; kind: VoiceErrorKind };

export const IDLE: VoiceState = { phase: "idle", error: null };

/** A recording shorter than this has no question in it. */
export const MIN_RECORDING_MS = 600;

const on = (phase: VoicePhase): VoiceState => ({ phase, error: null });

export function voiceReducer(state: VoiceState, event: VoiceEvent): VoiceState {
  const { phase } = state;
  switch (event.type) {
    case "mic_requested":
      // Pressing the microphone stops a reading and retries after an error; a recording under way is not restarted.
      return phase === "idle" || phase === "speaking" || phase === "error" ? on("asking_permission") : state;
    case "recording_started":
      return phase === "asking_permission" ? on("recording") : state;
    case "recording_stopped":
      if (phase !== "recording") return state;
      return event.elapsedMs < MIN_RECORDING_MS ? { phase: "error", error: "too_short" } : on("transcribing");
    case "transcribed":
      return phase === "transcribing" ? IDLE : state;
    case "speech_started":
      return phase === "idle" || phase === "error" ? on("speaking") : state;
    case "speech_ended":
      return phase === "speaking" ? IDLE : state;
    case "cancelled":
      return phase === "asking_permission" || phase === "recording" || phase === "transcribing" ? IDLE : state;
    case "failed":
      return { phase: "error", error: event.kind };
  }
}

/** True while the microphone or the transcription is in use: nothing else may start. */
export const isCapturing = (state: VoiceState): boolean =>
  state.phase === "asking_permission" || state.phase === "recording" || state.phase === "transcribing";
