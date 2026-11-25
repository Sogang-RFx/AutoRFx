/** When do the sphere and the shared layer ask for another frame? Invented numbers only. */
import { describe, expect, it } from "vitest";
import { sphereNeedsFrame, type SphereMotion } from "@/lib/fx/sphere";
import { layerNeedsFrame, layerWakesOnPointer } from "@/lib/fx/particles";

const still = (over: Partial<SphereMotion> = {}): SphereMotion => ({ mode: "idle", level: 0, target: 0, ripples: 0, isStill: false, ...over });

describe("sphereNeedsFrame", () => {
  it("idle draws one frame and stops", () => {
    expect(sphereNeedsFrame(still())).toBe(false);
  });

  it("thinking without a ripple is still", () => {
    expect(sphereNeedsFrame(still({ mode: "thinking" }))).toBe(false);
  });

  it("a live ripple keeps any mode moving until it has faded", () => {
    for (const mode of ["idle", "thinking", "listening", "speaking"] as const) {
      expect(sphereNeedsFrame(still({ mode, ripples: 1 }))).toBe(true);
    }
  });

  it("listening and speaking move while they run", () => {
    expect(sphereNeedsFrame(still({ mode: "listening" }))).toBe(true);
    expect(sphereNeedsFrame(still({ mode: "speaking" }))).toBe(true);
  });

  it("a level still settling after the mode went idle moves, a settled one does not", () => {
    expect(sphereNeedsFrame(still({ level: 0.3 }))).toBe(true);
    expect(sphereNeedsFrame(still({ level: 0.0004 }))).toBe(false);
  });

  it("a still sphere (effects off) never moves", () => {
    expect(sphereNeedsFrame(still({ mode: "speaking", ripples: 2, isStill: true }))).toBe(false);
  });
});

describe("layerNeedsFrame", () => {
  it("no dots: no frame, even if something claims to move", () => {
    expect(layerNeedsFrame({ dots: 0, moving: true, fading: true })).toBe(false);
  });

  it("dots that move or fade need frames; settled ones do not", () => {
    expect(layerNeedsFrame({ dots: 10, moving: true, fading: false })).toBe(true);
    expect(layerNeedsFrame({ dots: 10, moving: false, fading: true })).toBe(true);
    expect(layerNeedsFrame({ dots: 10, moving: false, fading: false })).toBe(false);
  });
});

describe("layerWakesOnPointer", () => {
  it("only when there are dots for the pointer to push", () => {
    expect(layerWakesOnPointer(0)).toBe(false);
    expect(layerWakesOnPointer(3)).toBe(true);
  });
});
