/** Point thinning and the pure shape samplers. Invented coordinates only. */
import { describe, expect, it } from "vitest";
import { seededRandom } from "@/lib/fx/motion";
import { capTotal, concat, pointCount, sampleBox, sampleCircle, sampleLine, sampleRule, thin, type Points } from "@/lib/fx/sample";

/** Reproducible scatter fixture. */
function scatterPoints(count: number, width: number, height: number, seed = 1): Points {
  const random = seededRandom(seed);
  const out = new Float32Array(count * 2);
  for (let i = 0; i < count; i += 1) {
    out[i * 2] = random() * width;
    out[i * 2 + 1] = random() * height;
  }
  return out;
}

describe("thin", () => {
  const points = scatterPoints(1000, 400, 300, 5);

  it("keeps exactly the cap and never more", () => {
    expect(pointCount(thin(points, 250, seededRandom(1)))).toBe(250);
    expect(pointCount(thin(points, 999, seededRandom(2)))).toBe(999);
  });

  it("returns everything when under the cap", () => {
    expect(thin(points, 5000, seededRandom(1))).toBe(points);
  });

  it("is deterministic with a seeded random source and differs between seeds", () => {
    const a = thin(points, 300, seededRandom(7));
    const b = thin(points, 300, seededRandom(7));
    const c = thin(points, 300, seededRandom(8));
    expect([...a]).toEqual([...b]);
    expect([...a]).not.toEqual([...c]);
  });

  it("keeps the original order and only original points", () => {
    const kept = thin(points, 100, seededRandom(3));
    const original = new Map<number, number>();
    for (let i = 0; i < pointCount(points); i += 1) original.set(points[i * 2], i);
    let previous = -1;
    for (let i = 0; i < pointCount(kept); i += 1) {
      const index = original.get(kept[i * 2]);
      expect(index).toBeDefined();
      expect(index as number).toBeGreaterThan(previous);
      previous = index as number;
    }
  });

  it("handles a zero cap", () => {
    expect(pointCount(thin(points, 0, seededRandom(1)))).toBe(0);
  });
});

describe("capTotal", () => {
  it("keeps the total under the cap and each list's share", () => {
    const lists = [scatterPoints(900, 10, 10, 1), scatterPoints(300, 10, 10, 2)];
    const capped = capTotal(lists, 600, seededRandom(1));
    const total = capped.reduce((sum, l) => sum + pointCount(l), 0);
    expect(total).toBeLessThanOrEqual(600);
    expect(total).toBeGreaterThan(590);
    expect(pointCount(capped[0])).toBeGreaterThan(pointCount(capped[1]) * 2.5);
  });

  it("leaves small totals alone", () => {
    const lists = [scatterPoints(10, 10, 10, 1)];
    expect(capTotal(lists, 600)[0]).toBe(lists[0]);
  });
});

describe("shapes", () => {
  it("a line has both ends and even steps", () => {
    const line = sampleLine(0, 0, 30, 0, 3);
    expect(pointCount(line)).toBe(11);
    expect([line[0], line[1]]).toEqual([0, 0]);
    expect([line[line.length - 2], line[line.length - 1]]).toEqual([30, 0]);
  });

  it("a circle's points lie on its radius", () => {
    const circle = sampleCircle(50, 60, 20, 3);
    for (let i = 0; i < pointCount(circle); i += 1) {
      expect(Math.hypot(circle[i * 2] - 50, circle[i * 2 + 1] - 60)).toBeCloseTo(20, 3);
    }
  });

  it("a box has edge points only and ignores tiny boxes", () => {
    const box = sampleBox({ left: 0, top: 0, right: 100, bottom: 50 });
    for (let i = 0; i < pointCount(box); i += 1) {
      const [x, y] = [box[i * 2], box[i * 2 + 1]];
      expect(x === 0 || x === 100 || y === 0 || y === 50).toBe(true);
    }
    expect(pointCount(sampleBox({ left: 0, top: 0, right: 2, bottom: 2 }))).toBe(0);
  });

  it("a rule runs along its long axis through the middle", () => {
    const rule = sampleRule({ left: 0, top: 10, right: 90, bottom: 12 });
    for (let i = 0; i < pointCount(rule); i += 1) expect(rule[i * 2 + 1]).toBe(11);
    const vertical = sampleRule({ left: 5, top: 0, right: 7, bottom: 90 });
    for (let i = 0; i < pointCount(vertical); i += 1) expect(vertical[i * 2]).toBe(6);
  });

  it("concat joins lists in order", () => {
    expect([...concat([Float32Array.of(1, 2), Float32Array.of(3, 4)])]).toEqual([1, 2, 3, 4]);
  });
});
