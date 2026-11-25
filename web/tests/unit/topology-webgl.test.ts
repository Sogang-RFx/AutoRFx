import { describe, expect, it } from "vitest";
import { flowOnReturn } from "@/lib/topology/flow";
import { shouldReportContextLoss } from "@/lib/topology/webgl";

describe("shouldReportContextLoss", () => {
  it("counts a loss while the scene is mounted", () => {
    expect(shouldReportContextLoss({ tornDown: false })).toBe(true);
  });
  it("ignores the loss our own teardown causes", () => {
    expect(shouldReportContextLoss({ tornDown: true })).toBe(false);
  });
});

describe("flowOnReturn", () => {
  it("does not replay the all-edges flow when coming back from the list", () => {
    const next = flowOnReturn({ token: 0, department: null });
    expect(next.token).toBeGreaterThan(0);
    expect(next.department).toBeNull();
  });
  it("does not replay a department's flow either", () => {
    const next = flowOnReturn({ token: 3, department: "d1" });
    expect(next).toEqual({ token: 3, department: null });
  });
});
