/** What the sphere shows: the microphone level while recording, the playback level while speaking, nothing invented. */
import type { SphereMode } from "@/lib/fx/sphere";
import type { VoicePhase } from "./state";

export interface OrbState {
  mode: SphereMode;
  level: number;
}

/** `isAwaitingAnswer`: a question was sent and the answer is being made. */
export function orbState(phase: VoicePhase, level: number, isAwaitingAnswer: boolean): OrbState {
  if (phase === "recording") return { mode: "listening", level };
  if (phase === "speaking") return { mode: "speaking", level };
  if (phase === "transcribing" || isAwaitingAnswer) return { mode: "thinking", level: 0 };
  return { mode: "idle", level: 0 };
}
