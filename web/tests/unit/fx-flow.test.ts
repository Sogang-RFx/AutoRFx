/** Flow gating: what the dots do follows the gate states and nothing else. */
import { describe, expect, it } from "vitest";
import { seededRandom } from "@/lib/fx/motion";
import { BUCKET, FlowField, isInTransit, locate, pickBucket, planBuckets, stepAlong, streamEnd, type FlowGate, type FlowOut, type GateState, type LanePoint } from "@/lib/fx/flow";
import { TONE } from "@/lib/fx/tone";

const lane = (...states: GateState[]): FlowGate[] => states.map((state, i) => ({ x: 100, y: i * 50, state }));
const out = (): FlowOut => ({ x: 0, y: 0, a: 1, tone: TONE.ink });

describe("planBuckets", () => {
  it("done gates hold dots, the active gate streams to the next gate", () => {
    const buckets = planBuckets(lane("done", "done", "active", "pending"));
    expect(buckets.map((b) => b.kind)).toEqual([BUCKET.rest, BUCKET.rest, BUCKET.stream]);
    expect(buckets[2]).toMatchObject({ from: 2, to: 3 });
  });

  it("an error gate holds dots in the danger style and nothing streams past it", () => {
    const buckets = planBuckets(lane("done", "error", "pending"));
    expect(buckets.map((b) => [b.kind, b.danger])).toEqual([
      [BUCKET.rest, false],
      [BUCKET.rest, true],
    ]);
  });

  it("skipped gates are never a resting place and are passed over by a stream", () => {
    const gates = lane("active", "skipped", "skipped", "pending");
    expect(streamEnd(gates, 0)).toBe(3);
    expect(planBuckets(gates)).toEqual([{ kind: BUCKET.stream, from: 0, to: 3, danger: false }]);
    expect(planBuckets(lane("done", "skipped", "done")).map((b) => b.to)).toEqual([0, 2]);
  });

  it("with no real state the dots wait at the first gate: nothing is made up", () => {
    expect(planBuckets(lane("pending", "pending"))).toEqual([{ kind: BUCKET.wait, from: 0, to: 0, danger: false }]);
    expect(planBuckets([])).toHaveLength(1);
  });

  it("an active last gate has nowhere to stream and rests", () => {
    expect(planBuckets(lane("done", "active"))[1].kind).toBe(BUCKET.rest);
  });

  it("splits the dots evenly by rank", () => {
    const buckets = planBuckets(lane("done", "done", "active", "pending"));
    expect(pickBucket(buckets, 0.0)).toBe(buckets[0]);
    expect(pickBucket(buckets, 0.4)).toBe(buckets[1]);
    expect(pickBucket(buckets, 0.99)).toBe(buckets[2]);
    expect(pickBucket(buckets, 1)).toBe(buckets[2]);
  });
});

describe("stepAlong", () => {
  it("a resting dot stops at its gate", () => {
    const bucket = { kind: BUCKET.rest, from: 2, to: 2, danger: false } as const;
    let s = 0;
    for (let i = 0; i < 200; i += 1) s = stepAlong(s, bucket, 1, 0.05);
    expect(s).toBe(2);
  });

  it("a resting dot never goes beyond its gate and comes back if the lane changed", () => {
    const bucket = { kind: BUCKET.rest, from: 1, to: 1, danger: false } as const;
    expect(stepAlong(3, bucket, 1, 0.5)).toBeLessThan(3);
    expect(stepAlong(3, bucket, 1, 10)).toBe(1);
  });

  it("a streaming dot loops inside its stream", () => {
    const bucket = { kind: BUCKET.stream, from: 2, to: 3, danger: false } as const;
    let s = 2;
    for (let i = 0; i < 400; i += 1) {
      s = stepAlong(s, bucket, 1.3, 0.05);
      expect(s).toBeGreaterThanOrEqual(2);
      expect(s).toBeLessThan(3);
    }
  });
});

describe("locate", () => {
  it("interpolates between gates and clamps at the ends", () => {
    const gates = lane("done", "done", "done");
    const p: LanePoint = { x: 0, y: 0, nx: 0, ny: 0 };
    expect(locate(gates, 0.5, p)).toMatchObject({ x: 100, y: 25 });
    expect(locate(gates, 9, p)).toMatchObject({ y: 100 });
    expect(locate(gates, -2, p)).toMatchObject({ y: 0 });
  });
});

describe("FlowField", () => {
  const settle = (field: FlowField, seconds: number) => {
    const o = out();
    for (let t = 0; t < seconds; t += 0.05) for (let i = 0; i < field.count; i += 1) field.advance(i, 0.05, o);
  };

  it("settles dots on done gates and keeps the others streaming", () => {
    const field = new FlowField(300, seededRandom(1));
    field.setLanes([{ gates: lane("done", "done", "active", "pending") }]);
    settle(field, 20);
    const places = new Map<string, number>();
    for (let i = 0; i < field.count; i += 1) {
      const b = field.bucketOf(i);
      places.set(b.kind + ":" + b.to, (places.get(b.kind + ":" + b.to) ?? 0) + 1);
      if (b.kind === BUCKET.rest) expect(field.placeOf(i)).toBe(b.to);
      else expect(field.placeOf(i)).toBeLessThan(3);
      expect(field.placeOf(i)).toBeLessThanOrEqual(3);
    }
    // Never a dot on or beyond the pending gate beyond the stream end.
    expect(places.size).toBe(3);
  });

  it("draws error dots in the danger tone only", () => {
    const field = new FlowField(200, seededRandom(2));
    field.setLanes([{ gates: lane("done", "error") }]);
    settle(field, 15);
    const o = out();
    let danger = 0;
    for (let i = 0; i < field.count; i += 1) {
      field.advance(i, 0.01, o);
      const isDanger = o.tone === TONE.danger;
      expect(isDanger).toBe(field.bucketOf(i).danger);
      if (isDanger) danger += 1;
    }
    expect(danger).toBeGreaterThan(60);
    expect(danger).toBeLessThan(140);
  });

  it("follows a state change: a gate that becomes done collects dots", () => {
    const field = new FlowField(200, seededRandom(3));
    field.setLanes([{ gates: lane("done", "active", "pending") }]);
    settle(field, 10);
    const before = [...Array(field.count).keys()].filter((i) => field.bucketOf(i).kind === BUCKET.rest && field.bucketOf(i).to === 1).length;
    field.setLanes([{ gates: lane("done", "done", "active") }]);
    settle(field, 15);
    const after = [...Array(field.count).keys()].filter((i) => field.bucketOf(i).kind === BUCKET.rest && field.bucketOf(i).to === 1);
    expect(before).toBe(0);
    expect(after.length).toBeGreaterThan(40);
    after.forEach((i) => expect(field.placeOf(i)).toBe(1));
  });

  it("hides dots that have no lane and shares dots by lane weight", () => {
    const field = new FlowField(400, seededRandom(4));
    field.setLanes([
      { gates: lane("done"), weight: 3 },
      { gates: lane("done", "done"), weight: 1 },
      { gates: [], weight: 5 },
    ]);
    const o = out();
    let hidden = 0;
    for (let i = 0; i < field.count; i += 1) if (field.advance(i, 0.01, o).a === 0) hidden += 1;
    expect(hidden).toBe(0);
    field.setLanes([]);
    for (let i = 0; i < field.count; i += 1) expect(field.advance(i, 0.01, o).a).toBe(0);
  });
});

describe("isInTransit", () => {
  it("is true while a gate is active with somewhere to stream to", () => {
    expect(isInTransit([{ gates: lane("done", "active", "pending") }])).toBe(true);
    expect(isInTransit([{ gates: lane("active", "skipped", "pending") }])).toBe(true);
  });

  it("is false when every gate is terminal or pending", () => {
    expect(isInTransit([{ gates: lane("done", "done", "done") }])).toBe(false);
    expect(isInTransit([{ gates: lane("done", "error", "pending") }])).toBe(false);
    expect(isInTransit([{ gates: lane("done", "skipped", "done") }])).toBe(false);
    expect(isInTransit([{ gates: lane("pending", "pending") }])).toBe(false);
  });

  it("an active last gate has nowhere to stream, so nothing is in transit", () => {
    expect(isInTransit([{ gates: lane("done", "active") }])).toBe(false);
  });

  it("is true when any one lane streams, false for no lanes or empty lanes", () => {
    expect(isInTransit([{ gates: lane("done", "done") }, { gates: lane("active", "pending") }])).toBe(true);
    expect(isInTransit([])).toBe(false);
    expect(isInTransit([{ gates: [] }])).toBe(false);
  });
});

describe("FlowField.isMoving", () => {
  const frame = (field: FlowField, dt = 0.05): boolean => {
    const o = out();
    field.startFrame();
    for (let i = 0; i < field.count; i += 1) field.advance(i, dt, o);
    return field.isMoving;
  };

  it("keeps moving while a gate is active", () => {
    const field = new FlowField(100, seededRandom(1));
    field.setLanes([{ gates: lane("done", "active", "pending") }]);
    for (let k = 0; k < 400; k += 1) expect(frame(field)).toBe(true);
  });

  it("settles and reports no movement once every gate is terminal", () => {
    const field = new FlowField(150, seededRandom(2));
    field.setLanes([{ gates: lane("done", "done", "active", "pending") }]);
    for (let k = 0; k < 40; k += 1) frame(field);
    field.setLanes([{ gates: lane("done", "done", "done", "done") }]);
    let frames = 0;
    while (frame(field) && frames < 2000) frames += 1;
    expect(frames).toBeLessThan(2000);
    // And it stays settled: a further frame moves nothing.
    expect(frame(field)).toBe(false);
    expect(frame(field)).toBe(false);
  });

  it("starts moving again when an active gate comes back", () => {
    const field = new FlowField(100, seededRandom(3));
    field.setLanes([{ gates: lane("done", "done") }]);
    for (let k = 0; k < 400; k += 1) frame(field);
    expect(frame(field)).toBe(false);
    field.setLanes([{ gates: lane("done", "active", "pending") }]);
    expect(frame(field)).toBe(true);
  });

  it("an error gate settles too", () => {
    const field = new FlowField(100, seededRandom(4));
    field.setLanes([{ gates: lane("done", "error", "pending") }]);
    let frames = 0;
    while (frame(field) && frames < 2000) frames += 1;
    expect(frame(field)).toBe(false);
  });

  it("dots with no lane never count as moving", () => {
    const field = new FlowField(50, seededRandom(5));
    field.setLanes([]);
    expect(frame(field)).toBe(false);
  });
});
