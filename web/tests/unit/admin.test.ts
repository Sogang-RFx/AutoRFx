/** Account screen helpers: only changes are sent, and a member always has a department. Values are invented. */
import { describe, expect, it } from "vitest";
import { createProblem, departmentCost, departmentScope, toCreateRequest, toPatch, type AccountDraft } from "@/lib/rfq/admin";
import { AdminUserCreateRequest, AdminUserPatchRequest, type AdminUser, type Department } from "@/lib/rfq/contract";

const USER: AdminUser = {
  id: "u-2",
  username: "quality",
  display_name: "품질 담당",
  role: "member",
  department: "quality",
  disabled: false,
  must_change_password: false,
  created_at: 1,
  last_login: null,
  sessions: 0,
};
const draft = (patch: Partial<AccountDraft> = {}): AccountDraft => ({
  display_name: "품질 담당",
  role: "member",
  department: "quality",
  ...patch,
});

describe("changing an account", () => {
  it("sends only what changed", () => {
    expect(toPatch(USER, draft())).toBeNull();
    expect(toPatch(USER, draft({ department: "legal" }))).toEqual({ department: "legal" });
    expect(toPatch(USER, draft({ display_name: "  품질 2 " }))).toEqual({ display_name: "품질 2" });
  });

  it("clears the department of an administrator in the same request", () => {
    const patch = toPatch(USER, draft({ role: "admin" }));
    expect(patch).toEqual({ role: "admin", department: null });
    expect(AdminUserPatchRequest.safeParse(patch).success).toBe(true);
  });
});

describe("creating an account", () => {
  const full = { ...draft(), username: "new-user", password: "temporary-1" };

  it("builds the request of the contract", () => {
    expect(AdminUserCreateRequest.safeParse(toCreateRequest(full)).success).toBe(true);
    expect(toCreateRequest({ ...full, role: "admin" }).department).toBeNull();
  });

  it("names the first problem", () => {
    expect(createProblem(full)).toBeNull();
    expect(createProblem({ ...full, username: "Bad Name" })).toContain("아이디");
    expect(createProblem({ ...full, display_name: " " })).toContain("이름");
    expect(createProblem({ ...full, password: "short" })).toContain("8자");
    expect(createProblem({ ...full, department: "" })).toContain("부서");
    expect(createProblem({ ...full, role: "admin", department: "" })).toBeNull();
  });
});

describe("scope of a department", () => {
  it("comes from the rules sent by the backend", () => {
    const d = (receives_all: boolean, cost_visible: boolean): Department => ({
      id: "x",
      label: "엑스",
      role: "r",
      receives_all,
      cost_visible,
    });
    // The two are independent: neither is derived from the other.
    expect([departmentScope(d(true, false)), departmentCost(d(true, false))]).toEqual(["전체", "열람하지 않음"]);
    expect([departmentScope(d(false, true)), departmentCost(d(false, true))]).toEqual(["자기 부서", "열람함"]);
  });
});
