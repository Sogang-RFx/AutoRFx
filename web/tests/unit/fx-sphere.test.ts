/** Sphere lattice, projection, ripples and the audio level helpers. */
import { describe, expect, it } from "vitest";
import { depthAlpha, depthScale, fibonacciLattice, project, rippleWeight, RIPPLE_MS, smoothLevel, SpikeDetector, type Projected } from "@/lib/fx/sphere";

const view = (): Projected => ({ x: 0, y: 0, depth: 0 });

describe("lattice", () => {
  it("has the requested number of unit vectors", () => {
    const lattice = fibonacciLattice(500);
    expect(lattice.length).toBe(1500);
    for (let i = 0; i < 500; i += 1) {
      expect(Math.hypot(lattice[i * 3], lattice[i * 3 + 1], lattice[i * 3 + 2])).toBeCloseTo(1, 5);
    }
  });

  it("covers the sphere evenly: both hemispheres hold about half", () => {
    const lattice = fibonacciLattice(1000);
    let north = 0;
    for (let i = 0; i < 1000; i += 1) if (lattice[i * 3 + 1] > 0) north += 1;
    expect(north).toBeGreaterThan(480);
    expect(north).toBeLessThan(520);
  });

  it("copes with one dot", () => {
    expect(fibonacciLattice(1).length).toBe(3);
  });
});

describe("projection", () => {
  it("the near side is deeper and larger than the far side", () => {
    const near = project(0, 0, 1, 0, 0, view());
    const far = project(0, 0, -1, 0, 0, view());
    expect(near.depth).toBeCloseTo(1, 5);
    expect(far.depth).toBeCloseTo(0, 5);
    const side = project(0.6, 0, 0.8, 0, 0, view());
    const back = project(0.6, 0, -0.8, 0, 0, view());
    expect(side.x).toBeGreaterThan(back.x);
  });

  it("yaw turns the front point sideways by a quarter turn", () => {
    const p = project(0, 0, 1, Math.PI / 2, 0, view());
    expect(p.depth).toBeCloseTo(0.5, 5);
    expect(Math.abs(p.x)).toBeGreaterThan(0.9);
  });

  it("shading grows with depth", () => {
    expect(depthAlpha(1)).toBeGreaterThan(depthAlpha(0));
    expect(depthScale(1)).toBeGreaterThan(depthScale(0));
    expect(depthAlpha(2)).toBe(depthAlpha(1));
  });
});

describe("ripples", () => {
  it("peak on the expanding ring and are gone when old", () => {
    const early = rippleWeight(0.05, 20);
    const nowhere = rippleWeight(2.5, 300);
    expect(early).toBeGreaterThan(0.5);
    expect(nowhere).toBeLessThan(0.001);
    expect(rippleWeight(0.5, RIPPLE_MS)).toBe(0);
    expect(rippleWeight(0.5, -5)).toBe(0);
  });

  it("move outward with age", () => {
    const ringAt = (age: number) => {
      let best = 0;
      let bestDist = 0;
      for (let d = 0; d <= 1.4; d += 0.01) {
        const w = rippleWeight(d, age);
        if (w > best) [best, bestDist] = [w, d];
      }
      return bestDist;
    };
    expect(ringAt(700)).toBeGreaterThan(ringAt(250));
  });
});

describe("level", () => {
  it("rises faster than it falls", () => {
    const up = smoothLevel(0, 1, 0.05);
    const down = 1 - smoothLevel(1, 0, 0.05);
    expect(up).toBeGreaterThan(down * 3);
  });

  it("stays within 0..1 even for wild input", () => {
    expect(smoothLevel(0.5, 9, 1)).toBeLessThanOrEqual(1);
    expect(smoothLevel(0.5, -9, 1)).toBeGreaterThanOrEqual(0);
  });

  it("detects a jump once, then waits 160 ms", () => {
    const spikes = new SpikeDetector();
    for (let t = 0; t < 1000; t += 16) expect(spikes.update(0.05, 0.016, t)).toBe(false);
    expect(spikes.update(0.9, 0.016, 1000)).toBe(true);
    expect(spikes.update(0.95, 0.016, 1050)).toBe(false);
    expect(spikes.update(0.95, 0.016, 1200)).toBe(true);
  });
});
