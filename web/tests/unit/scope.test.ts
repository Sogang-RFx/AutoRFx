/**
 * What an account sees, in words (docs/auth-rbac.md §2, §6): the scope line, the departments offered
 * by the view filter, and wording that does not say why a clause is locked. Values are invented.
 */
import { describe, expect, it } from "vitest";
import type { Department, RtmRow } from "@/lib/rfq/contract";
import { departmentBrief, exportNote, rtmCsv } from "@/lib/rfq/export";
import { LOCKED_CLAUSE, departmentOptions, departmentRole, inDepartment, isCostVisible, scopeLine, scopeName, seesWholeSource } from "@/lib/rfq/scope";
import { viewer } from "./helpers";

const DEPARTMENTS: Department[] = [
  { id: "sales", label: "영업", role: "총괄", receives_all: true, cost_visible: true },
  { id: "quality", label: "품질", role: "품질", receives_all: false, cost_visible: false },
  { id: "legal", label: "법무", role: "계약", receives_all: false, cost_visible: false },
];

const row = (clause_id: string, primary: string, refs: string[] = [], title = "제목"): RtmRow => ({
  kind: "clause",
  clause_id,
  source_doc: "d01",
  section_id: clause_id,
  title,
  primary_department: primary,
  reference_departments: refs,
  priority: "must",
  red_flag: null,
  page_number: 1,
  sentence_index: 1,
  anchor: null,
  citation_status: "verified",
});
const locked = (clause_id: string): RtmRow => ({ kind: "masked", clause_id, source_doc: "d01", section_id: clause_id });
const rows = [row("1", "quality"), row("2", "legal", ["quality"]), locked("3"), locked("4")];
const labels = { dept: (d: string) => DEPARTMENTS.find((x) => x.id === d)?.label ?? d, file: (f: string) => f };

describe("scope in words", () => {
  it("names the department of a member and the whole scope of the others", () => {
    expect(scopeName(viewer())).toBe("품질 부서 범위");
    expect(scopeName(viewer({ department: "sales", department_label: "영업", full_scope: true }))).toBe("전체 범위");
    expect(scopeName(viewer({ role: "admin", department: null, department_label: null, full_scope: true }))).toBe("전체 범위");
  });

  it("counts what is shown and what is locked", () => {
    expect(scopeLine(viewer(), rows)).toBe("품질 부서 범위 · 조항 2 / 자물쇠 2");
    expect(scopeLine(viewer({ full_scope: true, group: "cost_visible" }), rows.slice(0, 2))).toBe("전체 범위 · 조항 2");
  });

  it("shows the whole source only to an account with the full scope and cost visibility", () => {
    expect(seesWholeSource(viewer({ full_scope: true, group: "cost_visible" }))).toBe(true);
    expect(seesWholeSource(viewer({ full_scope: true }))).toBe(false);
    expect(seesWholeSource(viewer({ group: "cost_visible" }))).toBe(false);
    expect(isCostVisible(viewer({ group: "cost_visible" }))).toBe(true);
  });
});

describe("department view filter", () => {
  it("offers only departments of visible clauses, in the order of the rules", () => {
    expect(departmentOptions(rows, DEPARTMENTS).map((d) => d.id)).toEqual(["quality", "legal"]);
    expect(departmentOptions([locked("3")], DEPARTMENTS)).toEqual([]);
  });

  it("knows lead and reference departments from the clause itself", () => {
    const [own, asReference] = rows;
    expect(own.kind === "clause" && departmentRole(own, "quality")).toBe("primary");
    expect(asReference.kind === "clause" && departmentRole(asReference, "quality")).toBe("reference");
    expect(own.kind === "clause" && departmentRole(own, "legal")).toBeNull();
  });

  it("keeps lock rows under every department, so the filter does not tell whose they are", () => {
    const quality = DEPARTMENTS[1];
    expect(rows.filter((r) => inDepartment(r, quality)).map((r) => r.clause_id)).toEqual(["1", "2", "3", "4"]);
    expect(rows.filter((r) => inDepartment(r, DEPARTMENTS[2])).map((r) => r.clause_id)).toEqual(["2", "3", "4"]);
    expect(rows.filter((r) => inDepartment(r, DEPARTMENTS[0]))).toHaveLength(4);
    expect(rows.filter((r) => inDepartment(r, null))).toHaveLength(4);
  });
});

describe("exports", () => {
  const engines = { analysis: "rule-only", translation: "none", retrieval: "bm25", llm_connected: false };

  it("say which scope they were made with", () => {
    expect(exportNote(engines, "품질 부서 범위")).toMatch(/^규칙 기반 분석 \(LLM 미연결\).*만든 범위: 품질 부서 범위$/);
    expect(exportNote(null, "품질 부서 범위")).toBeNull();
  });

  it("lock rows without a reason", () => {
    const csv = rtmCsv(rows, labels, null);
    expect(csv).toContain(`3,d01,3,${LOCKED_CLAUSE}`);
    expect(csv).not.toMatch(/팀|금액/);
    expect(departmentBrief("품질", [], ["3", "4"], labels, null)).toContain(`${LOCKED_CLAUSE}: 3, 4`);
  });
});
