import { describe, expect, it } from "vitest";
import { fitCamera, gutterFor, worldToPixel } from "@/lib/topology/camera";
import { BOX, computeLayout } from "@/lib/topology/layout";
import { buildModel, type TopoModel } from "@/lib/topology/model";
import { DEPARTMENTS, documentOf, maskedClause, visibleClause } from "./topology-fixtures";

const IDS = ["quality", "purchasing", "legal"];

/** n clauses over the given files, leads rotating, every seventh hidden. */
function synthetic(n: number, files = ["d01"], hiddenEvery = 7): TopoModel {
  const clauses = Array.from({ length: n }, (_, i) => {
    const file = files[i % files.length];
    return hiddenEvery > 0 && i % hiddenEvery === 6
      ? maskedClause(String(i), file)
      : visibleClause({ id: String(i), file, lead: IDS[i % IDS.length] });
  });
  return buildModel({ files: documentOf(...files).files, clauses, consistency: null, departments: DEPARTMENTS });
}

describe("computeLayout", () => {
  it("is the same for the same input", () => {
    const a = computeLayout(synthetic(60, ["d01", "d02"]));
    const b = computeLayout(synthetic(60, ["d01", "d02"]));
    expect(Array.from(a.clauseXY)).toEqual(Array.from(b.clauseXY));
    expect(Array.from(a.edges)).toEqual(Array.from(b.edges));
    expect(a.files).toEqual(b.files);
  });

  it("puts files left of the clauses and departments right of them", () => {
    const m = synthetic(40, ["d01", "d02"]);
    const l = computeLayout(m);
    const xs = Array.from({ length: m.clauses.length }, (_, i) => l.clauseXY[i * 2]);
    for (const f of l.files) expect(f.x).toBeLessThan(Math.min(...xs));
    for (const d of l.departments) expect(d.x).toBeGreaterThan(Math.max(...xs));
  });

  it("gives every clause its own cell, with no overlap", () => {
    const m = synthetic(500, ["d01", "d02", "d03"]);
    const l = computeLayout(m);
    const seen = new Set<string>();
    for (let i = 0; i < m.clauses.length; i += 1) {
      seen.add(`${l.clauseXY[i * 2].toFixed(3)},${l.clauseXY[i * 2 + 1].toFixed(3)}`);
    }
    expect(seen.size).toBe(m.clauses.length);
  });

  it("keeps each file's boxes inside that file's vertical span, files stacked without overlap", () => {
    const m = synthetic(90, ["d01", "d02", "d03"]);
    const l = computeLayout(m);
    m.clauses.forEach((c, i) => {
      const sheet = l.files[c.file];
      const y = l.clauseXY[i * 2 + 1];
      expect(y).toBeLessThanOrEqual(sheet.y + sheet.h / 2 + 1e-4);
      expect(y).toBeGreaterThanOrEqual(sheet.y - sheet.h / 2 - 1e-4);
    });
    for (let f = 1; f < l.files.length; f += 1) {
      expect(l.files[f].y + l.files[f].h / 2).toBeLessThan(l.files[f - 1].y - l.files[f - 1].h / 2);
    }
  });

  it("draws an edge from each visible clause to a point on its own department sheet", () => {
    const m = synthetic(70, ["d01", "d02"]);
    const l = computeLayout(m);
    expect(l.edges.length).toBe(m.edges.length * 4);
    m.edges.forEach((e, i) => {
      const d = l.departments[e.department];
      expect(l.edges[i * 4]).toBeCloseTo(l.clauseXY[e.clause * 2] + BOX / 2, 4);
      expect(l.edges[i * 4 + 1]).toBeCloseTo(l.clauseXY[e.clause * 2 + 1], 4);
      expect(l.edges[i * 4 + 2]).toBeLessThan(d.x);
      expect(l.edges[i * 4 + 3]).toBeLessThanOrEqual(d.y + d.h / 2 + 1e-4);
      expect(l.edges[i * 4 + 3]).toBeGreaterThanOrEqual(d.y - d.h / 2 - 1e-4);
    });
  });

  it("lands the edges of one department on distinct points", () => {
    const m = synthetic(60);
    const l = computeLayout(m);
    const ends = new Set<string>();
    m.edges.forEach((_, i) => ends.add(`${m.edges[i].department}:${l.edges[i * 4 + 3].toFixed(4)}`));
    expect(ends.size).toBe(m.edges.length);
  });

  it("places hidden clauses after the visible ones, on rows of their own, and draws no edge for them", () => {
    const m = synthetic(50);
    const l = computeLayout(m);
    const visibleY = m.clauses.flatMap((c, i) => (c.isVisible ? [l.clauseXY[i * 2 + 1]] : []));
    const hiddenY = m.clauses.flatMap((c, i) => (c.isVisible ? [] : [l.clauseXY[i * 2 + 1]]));
    expect(hiddenY.length).toBeGreaterThan(0);
    expect(Math.max(...hiddenY)).toBeLessThan(Math.min(...visibleY) + 1e-4);
    expect(Math.max(...hiddenY)).toBeLessThan(Math.min(...visibleY) - 0.5);
    expect(m.edges.every((e) => m.clauses[e.clause].isVisible)).toBe(true);
  });

  it("puts a hidden clause where the counts say, whatever the hidden clause is", () => {
    // The same counts with the hidden ones at other places in the response give the same hidden positions.
    const mk = (hiddenAt: number[]) =>
      buildModel({
        files: documentOf("d01").files,
        clauses: Array.from({ length: 12 }, (_, i) =>
          hiddenAt.includes(i) ? maskedClause(`h${i}`, "d01") : visibleClause({ id: String(i), file: "d01", lead: "quality" }),
        ),
        consistency: null,
        departments: DEPARTMENTS,
      });
    const cells = (m: TopoModel) => {
      const l = computeLayout(m);
      return m.clauses
        .flatMap((c, i) => (c.isVisible ? [] : [`${l.clauseXY[i * 2]},${l.clauseXY[i * 2 + 1]}`]))
        .sort();
    };
    expect(cells(mk([1, 4, 9]))).toEqual(cells(mk([0, 5, 11])));
  });

  it("scales from nothing to thousands of clauses with finite, bounded coordinates", () => {
    for (const n of [0, 1, 3, 40, 3000]) {
      const m = synthetic(n, ["d01", "d02"], n > 10 ? 9 : 0);
      const l = computeLayout(m);
      expect(l.clauseXY.every(Number.isFinite)).toBe(true);
      expect(l.edges.every(Number.isFinite)).toBe(true);
      const { minX, maxX, minY, maxY } = l.bounds;
      expect(Number.isFinite(minX + maxX + minY + maxY)).toBe(true);
      expect(maxX - minX).toBeLessThan(5 * Math.sqrt(Math.max(n, 1)) + 60);
    }
  });

  it("makes blocks wider than tall for a big file", () => {
    const l = computeLayout(synthetic(1000, ["d01"], 0));
    expect(l.files[0].h).toBeLessThan(l.bounds.maxX - l.bounds.minX);
  });
});

describe("fitCamera", () => {
  const bounds = { minX: -10, maxX: 30, minY: -8, maxY: 8 };
  const size = { widthPx: 1000, heightPx: 600 };
  const fov = 35;

  it("fits the map between the label gutters, centred on the bounds", () => {
    const fit = fitCamera({ bounds, ...size, fovDeg: fov });
    expect(fit.target).toEqual([10, 0]);
    const left = worldToPixel([bounds.minX, 0], fit, size, fov)[0];
    const right = worldToPixel([bounds.maxX, 0], fit, size, fov)[0];
    expect(left).toBeGreaterThanOrEqual(fit.gutterPx - 1);
    expect(right).toBeLessThanOrEqual(size.widthPx - fit.gutterPx + 1);
  });

  it("fits a tall map by its height", () => {
    const tall = { minX: -2, maxX: 2, minY: -100, maxY: 100 };
    const fit = fitCamera({ bounds: tall, ...size, fovDeg: fov });
    const top = worldToPixel([0, tall.maxY], fit, size, fov)[1];
    expect(top).toBeGreaterThanOrEqual(0);
  });

  it("moves back for a bigger map and for a narrower canvas", () => {
    const base = fitCamera({ bounds, ...size, fovDeg: fov }).distance;
    const wide = fitCamera({ bounds: { ...bounds, maxX: 130 }, ...size, fovDeg: fov }).distance;
    const narrow = fitCamera({ bounds, widthPx: 320, heightPx: 600, fovDeg: fov }).distance;
    expect(wide).toBeGreaterThan(base);
    expect(narrow).toBeGreaterThan(base);
  });

  it("shrinks the gutter on a narrow screen", () => {
    expect(gutterFor(1200)).toBe(150);
    expect(gutterFor(320)).toBeLessThan(100);
  });

  it("survives a degenerate canvas", () => {
    const fit = fitCamera({ bounds, widthPx: 0, heightPx: 0, fovDeg: fov });
    expect(Number.isFinite(fit.distance)).toBe(true);
  });
});
