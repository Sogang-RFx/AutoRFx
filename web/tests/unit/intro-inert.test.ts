/** The screen under the intro is inert while it plays and is released on every path, repeatedly. */
import { describe, expect, it } from "vitest";
import { holdInert, type InertTarget } from "@/components/intro/intro-inert";

const fake = () => {
  const attrs = new Map<string, string>();
  const target: InertTarget = {
    setAttribute: (n, v) => void attrs.set(n, v),
    removeAttribute: (n) => void attrs.delete(n),
  };
  return { attrs, target };
};

describe("holdInert", () => {
  it("sets inert and removes it on release", () => {
    const { attrs, target } = fake();
    const release = holdInert(target);
    expect(attrs.has("inert")).toBe(true);
    release();
    expect(attrs.has("inert")).toBe(false);
  });

  it("releases safely more than once", () => {
    const { attrs, target } = fake();
    const release = holdInert(target);
    release();
    release();
    expect(attrs.has("inert")).toBe(false);
  });

  it("survives a Strict Mode double run (set, release, set, release)", () => {
    const { attrs, target } = fake();
    holdInert(target)();
    const second = holdInert(target);
    expect(attrs.has("inert")).toBe(true);
    second();
    expect(attrs.has("inert")).toBe(false);
  });

  it("ignores a missing target", () => {
    expect(() => holdInert(null)()).not.toThrow();
  });
});
