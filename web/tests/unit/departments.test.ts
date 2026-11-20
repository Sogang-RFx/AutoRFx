/**
 * Company departments (SPEC US10): the filter follows the company's departments when
 * there are some, and the settings are read and replaced through the backend.
 * All values here are invented for the test.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { OrgDepartmentsResponse, type Department, type Team } from "@/lib/rfq/contract";
import { departmentFilters } from "@/lib/rfq/departments";

const AREAS: Department[] = [
  { id: "sales", label: "영업", role: "총괄", receives_all: true },
  { id: "quality", label: "품질", role: "품질", receives_all: false },
  { id: "costing", label: "원가", role: "원가", receives_all: false },
];

describe("department filter", () => {
  it("uses the rule departments while the company has none", () => {
    const teams: Team[] = [{ id: "quality", label: "품질팀", group: "cost_restricted" }];
    expect(departmentFilters(teams, AREAS)).toEqual([
      { id: "sales", label: "영업", areas: ["sales"], receives_all: true },
      { id: "quality", label: "품질", areas: ["quality"], receives_all: false },
      { id: "costing", label: "원가", areas: ["costing"], receives_all: false },
    ]);
  });

  it("uses the company's departments, which receive all clauses when one area does", () => {
    const teams: Team[] = [
      { id: "org-0123456789ab", label: "견적팀", group: "cost_visible", areas: ["sales", "costing"] },
      { id: "org-ba9876543210", label: "품질기획팀", group: "cost_restricted", areas: ["quality"] },
    ];
    expect(departmentFilters(teams, AREAS)).toEqual([
      { id: "org-0123456789ab", label: "견적팀", areas: ["sales", "costing"], receives_all: true },
      { id: "org-ba9876543210", label: "품질기획팀", areas: ["quality"], receives_all: false },
    ]);
  });
});

describe("settings requests", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("reads with GET and replaces the whole list with PUT", async () => {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_RFQ_API_BASE", "http://api.test");
    const { rfqApi } = await import("@/lib/rfq/provider");
    const body = {
      data: { departments: [{ id: "org-0123456789ab", label: "견적팀", areas: ["sales"], cost_visible: false }] },
      provenance: { document: "real" },
    };
    expect(OrgDepartmentsResponse.safeParse(body).success).toBe(true);
    const seen: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      seen.push({ url, init });
      return new Response(JSON.stringify(body), { status: 200 });
    });

    await rfqApi.orgDepartments();
    const saved = await rfqApi.saveOrgDepartments({
      departments: [{ id: null, label: "견적팀", areas: ["sales"], cost_visible: false }],
    });

    expect(seen.map((s) => [s.url, s.init?.method ?? "GET"])).toEqual([
      ["http://api.test/org/departments", "GET"],
      ["http://api.test/org/departments", "PUT"],
    ]);
    expect(JSON.parse(String(seen[1].init?.body))).toEqual({
      departments: [{ id: null, label: "견적팀", areas: ["sales"], cost_visible: false }],
    });
    expect(saved.data.departments[0].label).toBe("견적팀");
  });
});
