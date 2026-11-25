/** Flight curve, easing, pointer repel and the CSS/JS motion values. All numbers are invented. */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  approach,
  COVER_FADE_MS,
  easeFlight,
  FLIGHT_JITTER_MS,
  FLIGHT_MAX_MS,
  FLIGHT_MIN_MS,
  flightPoint,
  REPEL_CUTOFF_SIGMAS,
  REPEL_PUSH_PX,
  REPEL_SIGMA_PX,
  repelForce,
  REVEAL_FADE_MS,
  seededRandom,
  type Vec2,
} from "@/lib/fx/motion";

const out = (): Vec2 => ({ x: NaN, y: NaN });

describe("flight curve", () => {
  it("starts and ends exactly on its endpoints", () => {
    const start = flightPoint(out(), 0, 10, 20, 300, 400, 0.2, 1.5, 2);
    const end = flightPoint(out(), 1, 10, 20, 300, 400, 0.2, 1.5, 2);
    expect(start).toEqual({ x: 10, y: 20 });
    expect(end).toEqual({ x: 300, y: 400 });
    expect(flightPoint(out(), -3, 10, 20, 300, 400, 0.2, 1, 0)).toEqual({ x: 10, y: 20 });
    expect(flightPoint(out(), 7, 10, 20, 300, 400, 0.2, 1, 0)).toEqual({ x: 300, y: 400 });
  });

  it("has no jump just before landing: the sway has died out", () => {
    const near = flightPoint(out(), 1 - 1e-6, 0, 0, 200, 100, -0.28, 3, 1);
    expect(Math.hypot(near.x - 200, near.y - 100)).toBeLessThan(0.05);
  });

  it("bends sideways with the sign of `bend`", () => {
    const left = flightPoint(out(), 0.4, 0, 0, 200, 0, 0.28, 0, 0);
    const right = flightPoint(out(), 0.4, 0, 0, 200, 0, -0.28, 0, 0);
    expect(Math.sign(left.y)).toBe(-Math.sign(right.y));
    expect(Math.abs(left.y)).toBeGreaterThan(1);
  });
});

describe("flight easing", () => {
  it("is 0 at 0, 1 at 1 and never decreases", () => {
    expect(easeFlight(0)).toBe(0);
    expect(easeFlight(1)).toBe(1);
    let previous = 0;
    for (let i = 1; i <= 200; i += 1) {
      const e = easeFlight(i / 200);
      expect(e).toBeGreaterThanOrEqual(previous);
      previous = e;
    }
  });

  it("clamps outside 0..1", () => {
    expect(easeFlight(-1)).toBe(0);
    expect(easeFlight(2)).toBe(1);
  });
});

describe("pointer repel", () => {
  it("falls off with distance", () => {
    const near = repelForce(out(), 10, 0, 0, 0);
    const mid = repelForce(out(), REPEL_SIGMA_PX, 0, 0, 0);
    const far = repelForce(out(), REPEL_SIGMA_PX * 2, 0, 0, 0);
    expect(near.x).toBeGreaterThan(mid.x);
    expect(mid.x).toBeGreaterThan(far.x);
    expect(near.x).toBeLessThanOrEqual(REPEL_PUSH_PX);
  });

  it("is exactly zero beyond the cut-off", () => {
    const beyond = REPEL_SIGMA_PX * REPEL_CUTOFF_SIGMAS + 0.01;
    expect(repelForce(out(), beyond, 0, 500, 500)).toEqual({ x: 0, y: 0 });
    expect(repelForce(out(), 0, -beyond, 500, 500)).toEqual({ x: 0, y: 0 });
  });

  it("pushes away from the pointer", () => {
    expect(repelForce(out(), 20, 0, 0, 0).x).toBeGreaterThan(0);
    expect(repelForce(out(), -20, 0, 0, 0).x).toBeLessThan(0);
    expect(repelForce(out(), 0, 0, 0, 0).y).toBeLessThan(0);
  });

  it("springs back: an offset shrinks toward zero and never overshoots", () => {
    let offset = 20;
    for (let i = 0; i < 30; i += 1) {
      const next = approach(offset, 0, 0.05);
      expect(next).toBeLessThan(offset);
      expect(next).toBeGreaterThanOrEqual(0);
      offset = next;
    }
    expect(offset).toBeLessThan(0.1);
  });
});

describe("seeded random", () => {
  it("repeats for the same seed and stays in [0, 1)", () => {
    const a = seededRandom(42);
    const b = seededRandom(42);
    for (let i = 0; i < 50; i += 1) {
      const v = a();
      expect(v).toBe(b());
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });
});

describe("motion tokens", () => {
  const css = readFileSync(path.resolve(__dirname, "../../src/app/globals.css"), "utf8");
  const token = (name: string) => Number(new RegExp(`--${name}:\\s*(\\d+)ms`).exec(css)?.[1]);

  it("match the numbers in lib/fx/motion.ts", () => {
    expect(token("dur-fx-flight-min")).toBe(FLIGHT_MIN_MS);
    expect(token("dur-fx-flight-max")).toBe(FLIGHT_MAX_MS);
    expect(token("dur-fx-jitter")).toBe(FLIGHT_JITTER_MS);
    expect(token("dur-fx-reveal")).toBe(REVEAL_FADE_MS);
    expect(token("dur-fx-cover")).toBe(COVER_FADE_MS);
  });
});
