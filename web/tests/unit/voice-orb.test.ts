/** The sphere's mode and level follow the voice phases; the level is real only while recording or speaking. */
import { describe, expect, it } from "vitest";
import { orbState } from "@/lib/voice/orb";

describe("orbState", () => {
  it("follows the microphone while recording and the playback while speaking", () => {
    expect(orbState("recording", 0.6, false)).toEqual({ mode: "listening", level: 0.6 });
    expect(orbState("speaking", 0.3, true)).toEqual({ mode: "speaking", level: 0.3 });
  });

  it("thinks while the speech is transcribed or the answer is made, with no level", () => {
    expect(orbState("transcribing", 0.5, false)).toEqual({ mode: "thinking", level: 0 });
    expect(orbState("idle", 0.5, true)).toEqual({ mode: "thinking", level: 0 });
  });

  it("rests otherwise, and shows no level it has not measured", () => {
    for (const phase of ["idle", "asking_permission", "error"] as const) {
      expect(orbState(phase, 0.9, false)).toEqual({ mode: "idle", level: 0 });
    }
  });
});
