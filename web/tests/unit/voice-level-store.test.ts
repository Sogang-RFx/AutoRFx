/** The voice level lives outside React state, so a level change reaches the orb and nothing else. */
import { describe, expect, it, vi } from "vitest";
import { createLevelStore } from "@/lib/voice/level-store";

describe("createLevelStore", () => {
  it("holds the level, clamped to 0..1", () => {
    const store = createLevelStore();
    expect(store.get()).toBe(0);
    store.set(0.4);
    expect(store.get()).toBe(0.4);
    store.set(3);
    expect(store.get()).toBe(1);
    store.set(-1);
    expect(store.get()).toBe(0);
    store.set(Number.NaN);
    expect(store.get()).toBe(0);
  });

  it("tells subscribers only when the value changed", () => {
    const store = createLevelStore();
    const seen = vi.fn();
    store.subscribe(seen);
    store.set(0.5);
    store.set(0.5);
    store.set(0.6);
    expect(seen).toHaveBeenCalledTimes(2);
  });

  it("stops telling a subscriber that left", () => {
    const store = createLevelStore();
    const seen = vi.fn();
    const leave = store.subscribe(seen);
    leave();
    store.set(0.9);
    expect(seen).not.toHaveBeenCalled();
  });
});
