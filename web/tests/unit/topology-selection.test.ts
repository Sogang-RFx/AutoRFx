import { describe, expect, it } from "vitest";
import { buildModel } from "@/lib/topology/model";
import { accentEdges, clauseTones, departmentTones, pickSegments, resolveSelection } from "@/lib/topology/selection";
import { departmentRows, fileRows, mapSummary } from "@/lib/topology/tables";
import { DEPARTMENTS, documentOf, maskedClause, visibleClause } from "./topology-fixtures";

const model = buildModel({
  files: documentOf("d01", "d02").files,
  clauses: [
    visibleClause({ id: "1", file: "d01", lead: "purchasing" }),
    maskedClause("2", "d01"),
    visibleClause({ id: "3", file: "d02", lead: "quality", flag: "일방 해지" }),
    visibleClause({ id: "4", file: "d02", lead: "quality", refs: ["legal"] }),
  ],
  consistency: null,
  departments: DEPARTMENTS,
});

describe("resolveSelection", () => {
  it("finds a visible clause and a department by id", () => {
    expect(resolveSelection(model, { kind: "clause", id: "d02:4" })).toEqual({ kind: "clause", index: 3 });
    expect(resolveSelection(model, { kind: "department", id: "quality" })).toEqual({ kind: "department", index: 0 });
  });
  it("does not select a hidden clause or an id that is gone", () => {
    expect(resolveSelection(model, { kind: "clause", id: "d01:2" })).toBeNull();
    expect(resolveSelection(model, { kind: "clause", id: "nope" })).toBeNull();
    expect(resolveSelection(model, { kind: "department", id: "nope" })).toBeNull();
    expect(resolveSelection(model, null)).toBeNull();
  });
});

describe("clauseTones", () => {
  it("is ink, danger for a flag and hidden for a lock when nothing is chosen", () => {
    expect(clauseTones(model, null)).toEqual(["ink", "hidden", "danger", "ink"]);
  });
  it("makes the chosen clause accent, even if it is flagged", () => {
    expect(clauseTones(model, { kind: "clause", index: 2 })[2]).toBe("accent");
  });
  it("makes a chosen department's clauses accent but keeps flags and locks as they are", () => {
    expect(clauseTones(model, { kind: "department", index: 0 })).toEqual(["ink", "hidden", "danger", "accent"]);
  });
});

describe("accentEdges and pickSegments", () => {
  it("redraws the edge of a chosen clause and the edges of a chosen department", () => {
    expect(accentEdges(model, { kind: "clause", index: 3 })).toEqual([2]);
    expect(accentEdges(model, { kind: "department", index: 0 })).toEqual([1, 2]);
    expect(accentEdges(model, null)).toEqual([]);
  });
  it("copies the endpoints of the chosen edges", () => {
    const edges = new Float32Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
    expect(Array.from(pickSegments(edges, [2, 0]))).toEqual([9, 10, 11, 12, 1, 2, 3, 4]);
  });
});

describe("tables", () => {
  it("counts lead, reference and flags per department", () => {
    expect(departmentRows(model)).toEqual([
      { id: "quality", label: "품질", lead: 2, ref: 0, redFlags: 1 },
      { id: "purchasing", label: "구매", lead: 1, ref: 0, redFlags: 0 },
      { id: "legal", label: "법무", lead: 0, ref: 1, redFlags: 0 },
    ]);
  });
  it("counts visible and hidden clauses per file", () => {
    expect(fileRows(model).map((f) => [f.tag, f.visible, f.hidden, f.redFlags])).toEqual([
      ["A", 1, 1, 0],
      ["B", 2, 0, 1],
    ]);
  });
  it("sums up the map and mentions the clauses that are not shown", () => {
    const line = mapSummary(model);
    expect(line).toContain("파일 2개");
    expect(line).toContain("조항 3개");
    expect(line).toContain("1개");
    expect(mapSummary({ ...model, hiddenClauses: 0 })).not.toContain("열람 권한");
  });
});

describe("departmentTones", () => {
  it("is ink everywhere when nothing is chosen", () => {
    expect(departmentTones(model, null)).toEqual(["ink", "ink", "ink"]);
  });
  it("makes a chosen department accent and fades the rest", () => {
    expect(departmentTones(model, { kind: "department", index: 1 })).toEqual(["muted", "accent", "muted"]);
  });
  it("makes the lead of a chosen clause accent, keeps its references ink and fades the rest", () => {
    // clause d02:4 leads with quality and refers to legal
    expect(departmentTones(model, { kind: "clause", index: 3 })).toEqual(["accent", "muted", "ink"]);
  });
});
