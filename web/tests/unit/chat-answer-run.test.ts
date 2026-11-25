/**
 * The run of an answer's reveal: statements wait with `data-fx="wait"`, come out of dots, and end with no
 * attribute. The safety net must not bring a shown answer back to opacity 0, and whatever the run put on the shared
 * layer must be gone when it is cancelled. Elements and layer are fakes; all text is invented.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { runAnswerReveal, type RevealFrame, type RevealLayer } from "@/components/chat/answer-run";
import { FLIGHT_JITTER_MS, FLIGHT_MIN_MS } from "@/lib/fx/motion";

const ATTR = "data-fx";

function fakeItem() {
  const attrs = new Map<string, string>();
  return {
    attrs,
    getAttribute: (n: string) => attrs.get(n) ?? null,
    setAttribute: (n: string, v: string) => void attrs.set(n, v),
    removeAttribute: (n: string) => void attrs.delete(n),
  };
}

type Item = ReturnType<typeof fakeItem>;
const items = (n: number): Item[] => Array.from({ length: n }, fakeItem);
const states = (list: Item[]) => list.map((i) => i.getAttribute(ATTR));

/** A layer whose flight ends after a given time, like the real one; calls are recorded. */
function fakeLayer(opts: { dotCount?: number; flightMs?: number } = {}) {
  const calls: string[] = [];
  const layer: RevealLayer & { calls: string[]; assembleArgs: unknown[] } = {
    calls,
    assembleArgs: [],
    dotCount: opts.dotCount ?? 0,
    assemble(groups, options) {
      calls.push("assemble");
      layer.assembleArgs.push({ groups, options });
      return new Promise<void>((resolve) => setTimeout(resolve, opts.flightMs ?? FLIGHT_MIN_MS + FLIGHT_JITTER_MS));
    },
    fadeTo(_target, ms) {
      calls.push(`fade:${_target}`);
      return new Promise<void>((resolve) => setTimeout(resolve, ms));
    },
    clear() {
      calls.push("clear");
    },
  };
  return layer;
}

function fakeFrame() {
  const log: string[] = [];
  const from = (k: number): readonly [number, number] => [k, k];
  const frame: RevealFrame & { log: string[] } = {
    log,
    pointsFor: () => new Float32Array([1, 2, 3, 4]),
    from,
    clip: { apply: () => void log.push("clip"), release: () => void log.push("unclip") },
  };
  return frame;
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("runAnswerReveal", () => {
  it("shows every statement and leaves no attribute behind", async () => {
    const list = items(3);
    const layer = fakeLayer();
    runAnswerReveal({ items: list, layer, ready: async () => fakeFrame() });
    expect(states(list)).toEqual(["wait", "wait", "wait"]);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(states(list)).toEqual([null, null, null]);
  });

  it("does not bring a shown answer back to opacity 0 when the safety net fires", async () => {
    const list = items(2);
    runAnswerReveal({ items: list, layer: fakeLayer(), ready: async () => fakeFrame() });
    // Released at about 2.3 s; the net was set for 4.8 s.
    await vi.advanceTimersByTimeAsync(3_000);
    expect(states(list)).toEqual([null, null]);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(states(list)).toEqual([null, null]);
  });

  it("still shows the statements when the fonts and the frame never come", async () => {
    const list = items(2);
    runAnswerReveal({ items: list, layer: fakeLayer(), ready: () => new Promise(() => undefined) });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(states(list)).toEqual(["in", "in"]);
  });

  it("starts the dots from the frame's start points, and clips the layer while they fly", async () => {
    const list = items(2);
    const layer = fakeLayer();
    const frame = fakeFrame();
    runAnswerReveal({ items: list, layer, ready: async () => frame });
    await vi.advanceTimersByTimeAsync(10);
    const { options } = layer.assembleArgs[0] as { options: { from: unknown } };
    expect(options.from).toBe(frame.from);
    expect(frame.log).toEqual(["clip"]);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(frame.log).toEqual(["clip", "unclip"]);
    expect(layer.calls.filter((c) => c === "clear")).toHaveLength(1);
  });

  it("clears the layer and the clip when it is cancelled in flight", async () => {
    const list = items(2);
    const layer = fakeLayer();
    const frame = fakeFrame();
    const run = runAnswerReveal({ items: list, layer, ready: async () => frame });
    await vi.advanceTimersByTimeAsync(200);
    expect(layer.calls).toContain("assemble");
    run.cancel();
    expect(layer.calls.filter((c) => c === "clear")).toHaveLength(1);
    expect(frame.log).toEqual(["clip", "unclip"]);
    expect(states(list)).toEqual([null, null]);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(states(list)).toEqual([null, null]);
    expect(layer.calls.filter((c) => c === "clear")).toHaveLength(1);
  });

  it("leaves the layer alone when it is cancelled before it drew anything, or after it finished", async () => {
    const early = fakeLayer();
    const first = runAnswerReveal({ items: items(1), layer: early, ready: () => new Promise(() => undefined) });
    first.cancel();
    expect(early.calls).toEqual([]);

    const done = fakeLayer();
    const second = runAnswerReveal({ items: items(1), layer: done, ready: async () => fakeFrame() });
    await vi.advanceTimersByTimeAsync(10_000);
    const clears = done.calls.filter((c) => c === "clear").length;
    second.cancel();
    expect(done.calls.filter((c) => c === "clear")).toHaveLength(clears);
  });

  it("does not touch a layer that already holds someone else's dots", async () => {
    const list = items(2);
    const layer = fakeLayer({ dotCount: 40 });
    const frame = fakeFrame();
    const run = runAnswerReveal({ items: list, layer, ready: async () => frame });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(layer.calls).toEqual([]);
    expect(frame.log).toEqual([]);
    expect(states(list)).toEqual([null, null]);
    run.cancel();
    expect(layer.calls).toEqual([]);
  });

  it("only fades the statements when there is no frame or no layer", async () => {
    const a = items(2);
    runAnswerReveal({ items: a, layer: fakeLayer(), ready: async () => null });
    const b = items(2);
    runAnswerReveal({ items: b, layer: null, ready: async () => fakeFrame() });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(states(a)).toEqual([null, null]);
    expect(states(b)).toEqual([null, null]);
  });

  it("frees the layer and shows the statements when something throws", async () => {
    const list = items(2);
    const layer = fakeLayer();
    const frame = fakeFrame();
    frame.pointsFor = () => {
      throw new Error("no canvas");
    };
    runAnswerReveal({ items: list, layer, ready: async () => frame });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(states(list)).toEqual([null, null]);
  });
});
