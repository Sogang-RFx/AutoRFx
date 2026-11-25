/** Processing flow: how a job's stage states become gate states, and what the dots then do. */
import { describe, expect, it } from "vitest";
import { BUCKET, planBuckets } from "@/lib/fx/flow";
import type { StageStatus } from "@/lib/rfq/contract";
import { gateState, RAIL_REST_GAP_PX, hasSeenRunning, railYs, stageLanes } from "@/app/rfq/[id]/processing/stage-gates";

const ys = (n: number) => Array.from({ length: n }, (_, i) => 20 + i * 60);

describe("gateState", () => {
  it("maps every job status to the gate state of the same meaning", () => {
    const expected: Record<StageStatus, string> = {
      pending: "pending",
      running: "active",
      done: "done",
      error: "error",
      skipped: "skipped",
    };
    for (const [status, state] of Object.entries(expected)) expect(gateState(status as StageStatus)).toBe(state);
  });
});

describe("hasSeenRunning", () => {
  const finished: StageStatus[] = ["done", "done", "skipped", "error"];

  it("stays false for a job opened already finished, failed or untouched", () => {
    expect(hasSeenRunning(false, finished)).toBe(false);
    expect(hasSeenRunning(false, Array<StageStatus>(8).fill("pending"))).toBe(false);
  });

  it("turns true on the first poll that shows a running stage", () => {
    expect(hasSeenRunning(false, ["done", "running", "pending"])).toBe(true);
  });

  it("stays true after the job settles", () => {
    expect(hasSeenRunning(true, finished)).toBe(true);
  });
});

describe("railYs", () => {
  it("is just under each icon on the connector, measured from the canvas top", () => {
    expect(railYs([100, 160, 230], 90)).toEqual([10 + RAIL_REST_GAP_PX, 70 + RAIL_REST_GAP_PX, 140 + RAIL_REST_GAP_PX]);
    expect(railYs([], 0)).toEqual([]);
  });
});

describe("stageLanes", () => {
  const eight: StageStatus[] = ["done", "done", "done", "running", "pending", "pending", "pending", "pending"];

  it("makes one lane of one gate per stage, at the rail x and the measured y", () => {
    const lanes = stageLanes(eight, 28, ys(8));
    expect(lanes).toHaveLength(1);
    expect(lanes[0].gates).toHaveLength(8);
    expect(lanes[0].gates[3]).toEqual({ x: 28, y: ys(8)[3], state: "active" });
  });

  it("is empty, so no dot is drawn, when the rows were not measured to match the stages", () => {
    expect(stageLanes(eight, 28, ys(5))).toEqual([]);
    expect(stageLanes([], 28, [])).toEqual([]);
  });

  it("streams from the running stage to the next and rests on the finished ones", () => {
    const buckets = planBuckets(stageLanes(eight, 28, ys(8))[0].gates);
    expect(buckets.map((b) => b.kind)).toEqual([BUCKET.rest, BUCKET.rest, BUCKET.rest, BUCKET.stream]);
    expect(buckets[3]).toMatchObject({ from: 3, to: 4, danger: false });
  });

  it("stops on a failed stage in the danger tone and streams nothing past it", () => {
    const failed: StageStatus[] = ["done", "done", "error", "pending", "pending"];
    const buckets = planBuckets(stageLanes(failed, 28, ys(5))[0].gates);
    expect(buckets.map((b) => [b.kind, b.danger])).toEqual([
      [BUCKET.rest, false],
      [BUCKET.rest, false],
      [BUCKET.rest, true],
    ]);
  });

  it("passes a skipped stage without resting on it", () => {
    const skipped: StageStatus[] = ["done", "running", "skipped", "skipped", "pending"];
    const buckets = planBuckets(stageLanes(skipped, 28, ys(5))[0].gates);
    expect(buckets).toEqual([
      { kind: BUCKET.rest, from: 0, to: 0, danger: false },
      { kind: BUCKET.stream, from: 1, to: 4, danger: false },
    ]);
  });

  it("when everything is done every dot rests and nothing streams", () => {
    const buckets = planBuckets(stageLanes(Array<StageStatus>(8).fill("done"), 28, ys(8))[0].gates);
    expect(buckets.every((b) => b.kind === BUCKET.rest)).toBe(true);
  });

  it("before anything started the dots only wait at the first gate: no motion is made up", () => {
    const buckets = planBuckets(stageLanes(Array<StageStatus>(8).fill("pending"), 28, ys(8))[0].gates);
    expect(buckets).toEqual([{ kind: BUCKET.wait, from: 0, to: 0, danger: false }]);
  });

  it("the last stage running rests at its own gate (there is no next one)", () => {
    const last: StageStatus[] = ["done", "done", "running"];
    const buckets = planBuckets(stageLanes(last, 28, ys(3))[0].gates);
    expect(buckets[buckets.length - 1]).toEqual({ kind: BUCKET.rest, from: 2, to: 2, danger: false });
  });
});
