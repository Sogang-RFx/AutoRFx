import { describe, expect, it } from "vitest";
import {
  FLOW_TOTAL_MS,
  FLOW_TRAVEL_MS,
  MAX_FLOW_DOTS,
  dotPositions,
  dotProgress,
  isFlowDone,
  sampleEdges,
} from "@/lib/topology/flow";

describe("sampleEdges", () => {
  it("keeps all ids when there are few", () => {
    expect(sampleEdges([4, 5, 6])).toEqual([4, 5, 6]);
  });
  it("spreads at most max ids evenly over a long list, in order, without repeats", () => {
    const ids = Array.from({ length: 5000 }, (_, i) => i);
    const out = sampleEdges(ids);
    expect(out).toHaveLength(MAX_FLOW_DOTS);
    expect(out[0]).toBe(0);
    expect(new Set(out).size).toBe(out.length);
    expect([...out].sort((a, b) => a - b)).toEqual(out);
    expect(out[out.length - 1]).toBeGreaterThan(4800);
  });
});

describe("dotProgress", () => {
  it("is null before a dot starts and after it arrived", () => {
    expect(dotProgress(5, 10, 0)).toBeNull();
    expect(dotProgress(0, 10, FLOW_TRAVEL_MS)).toBeNull();
  });
  it("rises from 0 to 1 and never goes back", () => {
    let last = -1;
    for (let t = 0; t < FLOW_TRAVEL_MS; t += 20) {
      const p = dotProgress(0, 10, t);
      expect(p).not.toBeNull();
      expect(p as number).toBeGreaterThanOrEqual(last);
      expect(p as number).toBeLessThanOrEqual(1);
      last = p as number;
    }
  });
  it("lets the last dot arrive exactly when the animation ends", () => {
    expect(dotProgress(9, 10, FLOW_TOTAL_MS - 1)).not.toBeNull();
    expect(dotProgress(9, 10, FLOW_TOTAL_MS)).toBeNull();
    expect(isFlowDone(FLOW_TOTAL_MS - 1)).toBe(false);
    expect(isFlowDone(FLOW_TOTAL_MS)).toBe(true);
  });
  it("starts a single dot at once and has nothing for no dots", () => {
    expect(dotProgress(0, 1, 1)).not.toBeNull();
    expect(dotProgress(0, 0, 100)).toBeNull();
  });
});

describe("dotPositions", () => {
  const edges = new Float32Array([0, 0, 10, 0, 0, 4, 10, 8]);
  it("moves each dot along its own edge and reports how many are on the way", () => {
    const out = new Float32Array(4);
    const shown = dotPositions(edges, [0, 1], 750, out);
    expect(shown).toBe(2);
    expect(out[0]).toBeGreaterThan(0);
    expect(out[0]).toBeLessThan(10);
    expect(out[1]).toBeCloseTo(0, 6);
    expect(out[3]).toBeGreaterThanOrEqual(4);
  });
  it("shows none once the flow is over", () => {
    expect(dotPositions(edges, [0, 1], FLOW_TOTAL_MS + 10, new Float32Array(4))).toBe(0);
  });
});
