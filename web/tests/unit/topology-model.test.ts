import { describe, expect, it } from "vitest";
import { buildModel } from "@/lib/topology/model";
import {
  DEPARTMENTS,
  clausesOf,
  consistencyOf,
  documentOf,
  maskedClause,
  visibleClause,
  visibleIssue,
} from "./topology-fixtures";

function model(consistency = null as ReturnType<typeof consistencyOf> | null) {
  return buildModel({
    files: documentOf("d01", "d02").files,
    clauses: clausesOf(
      visibleClause({ id: "1", file: "d01", lead: "purchasing", refs: ["quality"] }),
      maskedClause("2", "d01"),
      visibleClause({ id: "3", file: "d02", lead: "quality", flag: "일방 해지" }),
      visibleClause({ id: "4", file: "d02", lead: "purchasing", refs: ["legal", "quality"] }),
    ).clauses,
    consistency,
    departments: DEPARTMENTS,
  });
}

describe("buildModel", () => {
  it("keeps files in package order with tags and per-file counts", () => {
    const m = model();
    expect(m.files.map((f) => [f.id, f.tag, f.visible, f.hidden, f.redFlags])).toEqual([
      ["d01", "A", 1, 1, 0],
      ["d02", "B", 2, 0, 1],
    ]);
  });

  it("orders departments by the rules and counts lead and reference separately", () => {
    const m = model();
    expect(m.departments.map((d) => [d.id, d.label, d.lead, d.ref])).toEqual([
      ["quality", "품질", 1, 2],
      ["purchasing", "구매", 2, 0],
      ["legal", "법무", 0, 1],
    ]);
  });

  it("draws one edge per visible clause, from the clause to its lead department", () => {
    const m = model();
    expect(m.edges).toHaveLength(3);
    for (const e of m.edges) expect(m.clauses[e.clause].lead).toBe(e.department);
  });

  it("gives a hidden clause no department, no title and no edge (INV-8)", () => {
    const m = model();
    const hidden = m.clauses[1];
    expect(hidden).toMatchObject({ isVisible: false, lead: null, refs: [], title: "", number: "", priority: null, redFlag: null });
    expect(m.edges.some((e) => e.clause === 1)).toBe(false);
    expect(m.hiddenClauses).toBe(1);
  });

  it("does not guess a department the rules do not know", () => {
    const m = buildModel({
      files: documentOf("d01").files,
      clauses: [visibleClause({ id: "1", file: "d01", lead: "zeta" }), visibleClause({ id: "2", file: "d01", lead: "alpha" })],
      consistency: null,
      departments: DEPARTMENTS,
    });
    expect(m.departments.map((d) => [d.id, d.label])).toEqual([
      ["alpha", "alpha"],
      ["zeta", "zeta"],
    ]);
  });

  it("adds a file only the clauses name after the package files", () => {
    const m = buildModel({
      files: documentOf("d01").files,
      clauses: [visibleClause({ id: "1", file: "d09", lead: "quality" })],
      consistency: null,
      departments: DEPARTMENTS,
    });
    expect(m.files.map((f) => f.id)).toEqual(["d01", "d09"]);
    expect(m.clauses[0].file).toBe(1);
  });

  it("counts issues once per file their evidence is in, and hidden issues apart", () => {
    const m = model(
      consistencyOf([visibleIssue("i1", "d01", "d02", "d02"), visibleIssue("i2", "d02"), { kind: "masked", id: "i3" }]),
    );
    expect(m.files.map((f) => f.issues)).toEqual([1, 2]);
    expect(m.issues).toEqual({ visible: 2, hidden: 1 });
  });

  it("says nothing about issues when the consistency response is missing", () => {
    const m = model(null);
    expect(m.issues).toBeNull();
    expect(m.files.every((f) => f.issues === null)).toBe(true);
  });

  it("handles an empty clause list", () => {
    const m = buildModel({ files: documentOf("d01").files, clauses: [], consistency: null, departments: DEPARTMENTS });
    expect(m.clauses).toEqual([]);
    expect(m.departments).toEqual([]);
    expect(m.edges).toEqual([]);
  });
});
