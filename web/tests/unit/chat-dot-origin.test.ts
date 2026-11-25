/** Where the dots of an answer may start and land: only inside the conversation panel. All numbers are invented. */
import { describe, expect, it } from "vitest";
import {
  clampPoint,
  clipInset,
  fromContent,
  keepInside,
  startPointFor,
  toContent,
  type Box,
} from "@/components/chat/dot-origin";

const panel: Box = { left: 40, top: 80, right: 440, bottom: 600 };
const inside = (x: number, y: number, box: Box) => x >= box.left && x <= box.right && y >= box.top && y <= box.bottom;

describe("startPointFor", () => {
  it("starts every dot inside the rows of dots the waiting state showed", () => {
    const rows: Box[] = [
      { left: 60, top: 300, right: 300, bottom: 302 },
      { left: 60, top: 310, right: 300, bottom: 312 },
    ];
    for (let k = 0; k < 200; k += 1) {
      const [x, y] = startPointFor(k, rows, panel);
      expect(rows.some((r) => inside(x, y, r)), `dot ${k} at ${x},${y}`).toBe(true);
    }
  });

  it("uses every row, not just the first", () => {
    const rows: Box[] = [
      { left: 60, top: 300, right: 300, bottom: 302 },
      { left: 60, top: 400, right: 300, bottom: 402 },
    ];
    const ys = new Set(Array.from({ length: 20 }, (_, k) => (startPointFor(k, rows, panel)[1] < 350 ? "a" : "b")));
    expect(ys.size).toBe(2);
  });

  it("falls back to the box of the answer itself, and never leaves the panel", () => {
    const answer: Box = { left: 50, top: 200, right: 400, bottom: 320 };
    for (let k = 0; k < 100; k += 1) {
      const [x, y] = startPointFor(k, [answer], panel);
      expect(inside(x, y, answer)).toBe(true);
      expect(inside(x, y, panel)).toBe(true);
    }
  });

  it("pulls a source that sticks out of the panel back in", () => {
    const wide: Box = { left: -500, top: 20, right: 2000, bottom: 900 };
    for (let k = 0; k < 100; k += 1) {
      const [x, y] = startPointFor(k, [wide], panel);
      expect(inside(x, y, panel), `dot ${k} at ${x},${y}`).toBe(true);
    }
  });

  it("starts in the middle of the panel when it has no source at all", () => {
    const [x, y] = startPointFor(3, [], panel);
    expect(inside(x, y, panel)).toBe(true);
  });
});

describe("keepInside", () => {
  it("drops the target points that are outside, and keeps the order of the rest", () => {
    const points = new Float32Array([50, 100, 10, 100, 100, 700, 300, 500, 441, 300]);
    expect([...keepInside(points, panel)]).toEqual([50, 100, 300, 500]);
  });

  it("returns an empty list when nothing is inside", () => {
    expect(keepInside(new Float32Array([0, 0, 1000, 1000]), panel).length).toBe(0);
  });
});

describe("clampPoint", () => {
  it("moves a point to the nearest edge", () => {
    expect(clampPoint(0, 0, panel)).toEqual([40, 80]);
    expect(clampPoint(900, 900, panel)).toEqual([440, 600]);
    expect(clampPoint(100, 200, panel)).toEqual([100, 200]);
  });
});

describe("clipInset", () => {
  it("cuts the full-screen layer down to the panel", () => {
    expect(clipInset(panel, { width: 1000, height: 700 })).toBe("inset(80px 560px 100px 40px)");
  });

  it("never gives a negative inset when the panel is partly off screen", () => {
    expect(clipInset({ left: -20, top: -10, right: 1200, bottom: 900 }, { width: 1000, height: 700 })).toBe(
      "inset(0px 0px 0px 0px)",
    );
  });
});

describe("content coordinates", () => {
  const log = { left: 40, top: 80, scrollLeft: 0, scrollTop: 0 };
  const row: Box = { left: 60, top: 300, right: 300, bottom: 302 };

  it("follows the log when it scrolls after the rows were measured", () => {
    const saved = toContent(row, log);
    expect(fromContent(saved, { ...log, scrollTop: 0 })).toEqual(row);
    expect(fromContent(saved, { ...log, scrollTop: 50 })).toEqual({ ...row, top: 250, bottom: 252 });
  });

  it("follows the log when the page itself moved", () => {
    const saved = toContent(row, log);
    expect(fromContent(saved, { ...log, top: 30 })).toEqual({ ...row, top: 250, bottom: 252 });
  });
});
