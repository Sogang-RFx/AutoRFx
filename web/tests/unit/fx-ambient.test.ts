/** The background dot field: density, flow field, one dot step and when a frame is needed. Invented numbers only. */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  ALPHA_MAX,
  ALPHA_MIN,
  ambientFrameMode,
  createDots,
  dotCountFor,
  DOT_AREA_PX2,
  DOT_SIZES_PX,
  flowAngle,
  flowDirection,
  levelAlpha,
  MAX_DOTS,
  resizeDots,
  SPEED_MAX_PX,
  SPEED_MIN_PX,
  stepDot,
  WRAP_MARGIN_PX,
  wrapCoord,
  type PushSource,
} from "@/lib/fx/ambient";
import { TONE } from "@/lib/fx/tone";
import { seededRandom } from "@/lib/fx/motion";

const NO_POINTER: PushSource = { x: 0, y: 0, vx: 0, vy: 0, on: false };

describe("dotCountFor", () => {
  it("is about one dot per 1,400 px²", () => {
    expect(dotCountFor(1000, 700)).toBe(Math.round((1000 * 700) / DOT_AREA_PX2));
    expect(dotCountFor(700, 1000)).toBe(dotCountFor(1000, 700));
  });

  it("never exceeds 1,600, however large the screen", () => {
    expect(dotCountFor(3840, 2160)).toBe(MAX_DOTS);
    expect(dotCountFor(1e6, 1e6)).toBe(MAX_DOTS);
  });

  it("is never zero on a real screen, even a tiny one", () => {
    expect(dotCountFor(320, 480)).toBeGreaterThan(0);
    expect(dotCountFor(4, 4)).toBe(1);
  });

  it("is zero only when there is no area (or the size is not a number)", () => {
    expect(dotCountFor(0, 800)).toBe(0);
    expect(dotCountFor(800, -1)).toBe(0);
    expect(dotCountFor(Number.NaN, 800)).toBe(0);
  });

  it("grows with the area", () => {
    expect(dotCountFor(1440, 900)).toBeGreaterThan(dotCountFor(768, 1024) - 1);
    expect(dotCountFor(1440, 900)).toBeGreaterThan(dotCountFor(320, 568));
  });
});

describe("flow field", () => {
  it("gives a unit direction everywhere", () => {
    const out = { x: 0, y: 0 };
    for (const [x, y, t] of [[0, 0, 0], [123.4, 567.8, 9.1], [-40, 2000, 300], [1e4, 1e4, 1e4]]) {
      flowDirection(out, x, y, t);
      expect(Math.hypot(out.x, out.y)).toBeCloseTo(1, 6);
    }
  });

  it("changes smoothly in space: neighbours 1 px apart point almost the same way", () => {
    let worst = 0;
    for (let x = 0; x < 1200; x += 37) {
      for (let y = 0; y < 800; y += 41) {
        worst = Math.max(worst, Math.abs(flowAngle(x + 1, y, 5) - flowAngle(x, y, 5)), Math.abs(flowAngle(x, y + 1, 5) - flowAngle(x, y, 5)));
      }
    }
    expect(worst).toBeLessThan(0.05);
  });

  it("changes slowly in time: a tenth of a second turns it by a hair", () => {
    let worst = 0;
    for (let x = 0; x < 1200; x += 53) {
      for (let y = 0; y < 800; y += 59) worst = Math.max(worst, Math.abs(flowAngle(x, y, 12.1) - flowAngle(x, y, 12)));
    }
    expect(worst).toBeLessThan(0.06);
  });

  it("does change over time, so the field is not frozen", () => {
    expect(Math.abs(flowAngle(300, 200, 0) - flowAngle(300, 200, 30))).toBeGreaterThan(0.1);
  });

  it("is deterministic", () => {
    expect(flowAngle(10, 20, 3)).toBe(flowAngle(10, 20, 3));
  });
});

describe("wrapCoord", () => {
  it("leaves a point inside the screen and its margin alone", () => {
    expect(wrapCoord(0, 100)).toBe(0);
    expect(wrapCoord(100 + WRAP_MARGIN_PX - 1, 100)).toBe(100 + WRAP_MARGIN_PX - 1);
  });

  it("re-enters on the opposite side", () => {
    expect(wrapCoord(100 + WRAP_MARGIN_PX + 3, 100)).toBeCloseTo(-WRAP_MARGIN_PX + 3, 6);
    expect(wrapCoord(-WRAP_MARGIN_PX - 3, 100)).toBeCloseTo(100 + WRAP_MARGIN_PX - 3, 6);
  });

  it("handles a point far outside (after a resize)", () => {
    const v = wrapCoord(5000, 100);
    expect(v).toBeGreaterThanOrEqual(-WRAP_MARGIN_PX);
    expect(v).toBeLessThan(100 + WRAP_MARGIN_PX);
  });
});

describe("dots", () => {
  it("fills the field with the right mix of tone, size and opacity", () => {
    const d = createDots();
    resizeDots(d, 1440, 900, seededRandom(7));
    expect(d.count).toBe(dotCountFor(1440, 900));
    let muted = 0;
    for (let i = 0; i < d.count; i += 1) {
      expect([TONE.ink, TONE.muted]).toContain(d.tone[i]);
      if (d.tone[i] === TONE.muted) muted += 1;
      expect(d.size[i]).toBeLessThan(DOT_SIZES_PX.length);
      expect(d.speed[i]).toBeGreaterThanOrEqual(SPEED_MIN_PX);
      expect(d.speed[i]).toBeLessThanOrEqual(SPEED_MAX_PX);
      expect(d.x[i]).toBeGreaterThanOrEqual(-WRAP_MARGIN_PX);
      expect(d.x[i]).toBeLessThan(1440 + WRAP_MARGIN_PX);
      expect(d.y[i]).toBeLessThan(900 + WRAP_MARGIN_PX);
    }
    expect(muted / d.count).toBeGreaterThan(0.1);
    expect(muted / d.count).toBeLessThan(0.35);
  });

  it("keeps the opacity between its two limits", () => {
    const d = createDots();
    resizeDots(d, 800, 600, seededRandom(3));
    for (let i = 0; i < d.count; i += 1) {
      const a = levelAlpha(d.level[i]);
      expect(a).toBeGreaterThanOrEqual(ALPHA_MIN - 1e-9);
      expect(a).toBeLessThanOrEqual(ALPHA_MAX + 1e-9);
    }
    expect(levelAlpha(0)).toBeCloseTo(ALPHA_MIN, 9);
  });

  it("keeps the dots it has when the window changes size, and only adds or drops the difference", () => {
    const d = createDots();
    const random = seededRandom(11);
    resizeDots(d, 800, 600, random);
    const before = Array.from(d.x.slice(0, 50));
    const small = d.count;
    resizeDots(d, 1600, 1200, random);
    expect(d.count).toBe(dotCountFor(1600, 1200));
    expect(d.count).toBeGreaterThan(small);
    expect(Array.from(d.x.slice(0, 50))).toEqual(before);
    resizeDots(d, 800, 600, random);
    expect(d.count).toBe(small);
    expect(Array.from(d.x.slice(0, 50))).toEqual(before);
  });

  it("never holds more than 1,600", () => {
    const d = createDots();
    resizeDots(d, 5000, 5000, seededRandom(1));
    expect(d.count).toBe(MAX_DOTS);
  });

  it("is empty without a screen", () => {
    const d = createDots();
    resizeDots(d, 800, 600, seededRandom(1));
    resizeDots(d, 0, 0, seededRandom(1));
    expect(d.count).toBe(0);
  });
});

describe("stepDot", () => {
  const place = (x: number, y: number, speed = 10) => {
    const d = createDots();
    d.count = 1;
    d.x[0] = x;
    d.y[0] = y;
    d.speed[0] = speed;
    return d;
  };

  it("drifts at its own speed: 6 to 14 px a second", () => {
    for (const speed of [SPEED_MIN_PX, 10, SPEED_MAX_PX]) {
      const d = place(500, 400, speed);
      stepDot(d, 0, 1, 2, 1000, 800, NO_POINTER);
      expect(Math.hypot(d.x[0] - 500, d.y[0] - 400)).toBeCloseTo(speed, 3);
    }
  });

  it("does not move when no time passed", () => {
    const d = place(500, 400);
    stepDot(d, 0, 0, 2, 1000, 800, NO_POINTER);
    expect([d.x[0], d.y[0], d.ox[0], d.oy[0]]).toEqual([500, 400, 0, 0]);
  });

  it("wraps around the edge", () => {
    const d = place(1000 + WRAP_MARGIN_PX - 0.001, 400, SPEED_MAX_PX);
    // Find a time at which the flow points right, so the dot has to cross the edge.
    let t = 0;
    const dir = { x: 0, y: 0 };
    while (flowDirection(dir, d.x[0], d.y[0], t).x < 0.5 && t < 500) t += 0.5;
    // A step short enough that the dot travels less than the margin, whatever the speed limits are.
    const dt = WRAP_MARGIN_PX / (4 * SPEED_MAX_PX);
    stepDot(d, 0, dt, t, 1000, 800, NO_POINTER);
    expect(d.x[0]).toBeLessThan(0);
    expect(d.x[0]).toBeGreaterThanOrEqual(-WRAP_MARGIN_PX);
  });

  it("yields to the pointer", () => {
    const d = place(500, 400);
    const pointer: PushSource = { x: 480, y: 400, vx: 0, vy: 0, on: true };
    for (let k = 0; k < 20; k += 1) stepDot(d, 0, 0.016, 2, 1000, 800, pointer);
    // The pointer is to the left, so the dot is pushed to the right.
    expect(d.ox[0]).toBeGreaterThan(5);
  });

  it("ignores a pointer that is far away", () => {
    const d = place(500, 400);
    const pointer: PushSource = { x: 50, y: 50, vx: 0, vy: 0, on: true };
    for (let k = 0; k < 20; k += 1) stepDot(d, 0, 0.016, 2, 1000, 800, pointer);
    expect(Math.hypot(d.ox[0], d.oy[0])).toBe(0);
  });

  it("springs back once the pointer is gone, without overshooting", () => {
    const d = place(500, 400);
    d.ox[0] = 20;
    d.oy[0] = -12;
    let last = Math.hypot(20, 12);
    for (let k = 0; k < 120; k += 1) {
      stepDot(d, 0, 0.016, 2, 1000, 800, NO_POINTER);
      const now = Math.hypot(d.ox[0], d.oy[0]);
      expect(now).toBeLessThanOrEqual(last + 1e-9);
      last = now;
    }
    expect(last).toBeLessThan(0.01);
  });
});

describe("ambientFrameMode", () => {
  const on = { enabled: true, reduced: false, hidden: false, dots: 100, stillDrawn: false };

  it("runs while the tab is visible and effects are on", () => {
    expect(ambientFrameMode(on)).toBe("run");
    expect(ambientFrameMode({ ...on, stillDrawn: true })).toBe("run");
  });

  it("never draws while the tab is hidden", () => {
    expect(ambientFrameMode({ ...on, hidden: true })).toBe("none");
    expect(ambientFrameMode({ ...on, hidden: true, reduced: true })).toBe("none");
  });

  it("draws exactly one still frame under reduced motion", () => {
    expect(ambientFrameMode({ ...on, reduced: true })).toBe("still");
    expect(ambientFrameMode({ ...on, reduced: true, stillDrawn: true })).toBe("none");
  });

  it("draws nothing with effects off or without dots", () => {
    expect(ambientFrameMode({ ...on, enabled: false })).toBe("none");
    expect(ambientFrameMode({ ...on, enabled: false, reduced: true })).toBe("none");
    expect(ambientFrameMode({ ...on, dots: 0 })).toBe("none");
  });
});

describe("stacking and tokens", () => {
  const css = readFileSync(path.join(__dirname, "../../src/app/globals.css"), "utf8");

  it("puts the field below the content and the shared layer above it", () => {
    const ambient = /--z-fx-ambient:\s*(-?\d+)/.exec(css);
    const layer = /--z-fx-layer:\s*(\d+)/.exec(css);
    expect(ambient).not.toBeNull();
    expect(Number(ambient?.[1])).toBeLessThan(0);
    expect(Number(layer?.[1])).toBeGreaterThan(0);
  });

  it("never catches a click", () => {
    const block = /@utility fx-ambient \{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(block).toContain("pointer-events: none");
    expect(block).toContain("position: fixed");
    expect(block).toContain("z-index: var(--z-fx-ambient)");
    expect(block).not.toMatch(/background/);
  });
});
