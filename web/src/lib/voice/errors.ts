/**
 * What went wrong with the voice feature, in plain Korean with what to do next. The provider's own words are
 * not shown: the server already keeps them out of its messages, and the screen has better advice.
 */
import { ApiError } from "@/lib/rfq/provider";
import type { VoiceErrorKind } from "./state";

/** The browser has no microphone API or no recorder (an insecure page, an old browser). */
export class VoiceUnsupportedError extends Error {
  constructor() {
    super("recording is not supported here");
    this.name = "VoiceUnsupportedError";
  }
}

export type VoiceStep = "record" | "transcribe" | "speak";

const TOO_BIG = new Set([413, 422]);

/** The kind of a failure, or null for an abort (not an error). */
export function voiceErrorOf(e: unknown, step: VoiceStep): VoiceErrorKind | null {
  if (e instanceof VoiceUnsupportedError) return "unsupported";
  if (e instanceof DOMException) {
    if (e.name === "AbortError") return null;
    // The same name means a refused microphone when recording and a blocked autoplay when playing.
    if (e.name === "NotAllowedError" && step === "speak") return "playback_blocked";
    if (e.name === "NotAllowedError" || e.name === "SecurityError") return "permission_denied";
    if (e.name === "NotFoundError" || e.name === "OverconstrainedError") return "no_microphone";
    if (e.name === "NotReadableError") return "mic_busy";
    return "failed";
  }
  if (e instanceof ApiError) {
    if (e.status === 503 || e.status === 0) return "unavailable";
    if (TOO_BIG.has(e.status)) return step === "speak" ? "speech_too_long" : "too_long";
    if (e.status === 415) return "unsupported";
  }
  return "failed";
}

const MESSAGES: Record<VoiceErrorKind, string> = {
  permission_denied:
    "마이크 사용이 허용되지 않았습니다. 주소창 옆의 사이트 설정에서 마이크를 허용한 뒤 다시 눌러 주세요. 글로 질문하셔도 됩니다.",
  no_microphone: "사용할 수 있는 마이크를 찾지 못했습니다. 마이크가 연결되어 있는지 확인한 뒤 다시 눌러 주세요.",
  mic_busy: "마이크를 다른 프로그램이 쓰고 있습니다. 그 프로그램을 닫고 다시 눌러 주세요.",
  unsupported: "이 브라우저에서는 녹음을 쓸 수 없습니다. 다른 브라우저를 쓰시거나 글로 질문해 주세요.",
  too_short: "녹음이 너무 짧습니다. 마이크를 누르고 질문을 끝까지 말씀하신 뒤 다시 눌러 주세요.",
  too_long: "녹음이 너무 깁니다. 질문을 짧게 나눠서 다시 말씀해 주세요.",
  speech_too_long: "답이 너무 길어서 읽어 드리지 못했습니다. 화면의 글로 확인해 주세요.",
  playback_blocked: "브라우저가 소리 재생을 막았습니다. 화면을 한 번 누른 뒤 답변을 다시 읽도록 질문해 주세요.",
  no_speech: "말씀하신 내용을 알아듣지 못했습니다. 조금 더 또렷하게 다시 말씀해 주세요.",
  unavailable: "음성 기능을 지금은 쓸 수 없습니다. 잠시 뒤에 다시 시도하시거나 글로 질문해 주세요.",
  failed: "음성을 처리하지 못했습니다. 다시 시도해 주세요.",
};

export const voiceMessage = (kind: VoiceErrorKind): string => MESSAGES[kind];
