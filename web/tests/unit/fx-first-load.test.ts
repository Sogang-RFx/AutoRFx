/** Hard load versus client navigation: server-painted text is left alone, later mounts are revealed. */
import { describe, expect, it } from "vitest";
import { createLoadGate, shouldRevealOnMount } from "@/lib/fx/first-load";

describe("shouldRevealOnMount", () => {
  it("skips what the first commit of the document painted from server HTML", () => {
    expect(shouldRevealOnMount({ isFirstCommit: true })).toBe(false);
  });

  it("reveals anything that mounts after that (client-side navigation)", () => {
    expect(shouldRevealOnMount({ isFirstCommit: false })).toBe(true);
  });
});

describe("load gate", () => {
  function manualGate() {
    const queue: Array<() => void> = [];
    return { gate: createLoadGate((fn) => void queue.push(fn)), tick: () => queue.splice(0).forEach((fn) => fn()), queue };
  }

  it("does not reveal in the first commit, including Strict Mode's second run of it", () => {
    const { gate } = manualGate();
    expect(gate.shouldReveal()).toBe(false);
    gate.endFirstCommit(); // the provider's layout effect
    expect(gate.shouldReveal()).toBe(false);
    gate.endFirstCommit(); // Strict Mode runs the effect again in the same task
    expect(gate.shouldReveal()).toBe(false);
  });

  it("reveals from the next tick on", () => {
    const { gate, tick } = manualGate();
    gate.endFirstCommit();
    tick();
    expect(gate.shouldReveal()).toBe(true);
  });

  it("schedules the end of the first commit only once", () => {
    const { gate, queue } = manualGate();
    gate.endFirstCommit();
    gate.endFirstCommit();
    expect(queue).toHaveLength(1);
  });

  it("keeps skipping while the provider has not finished its first commit", () => {
    const { gate, tick } = manualGate();
    tick();
    expect(gate.shouldReveal()).toBe(false);
  });
});
