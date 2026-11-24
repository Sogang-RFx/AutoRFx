/**
 * Requests and sessions (docs/auth-rbac.md §3, §6): the cookie goes with every request, no request
 * says which team is looking, and one hook hears about a session the server no longer accepts.
 * All values here are invented for the test.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AdminUserCreateRequest, CompanyRequest, PrecostRequest } from "@/lib/rfq/contract";
import { API, apiError, json, viewer } from "./helpers";

type Provider = typeof import("@/lib/rfq/provider");
type Seen = { url: string; init: RequestInit };

let provider: Provider;
let seen: Seen[];

function answer(make: (url: string) => Response | Promise<Response>) {
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    seen.push({ url, init });
    return make(url);
  });
}

beforeEach(async () => {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_RFQ_API_BASE", API);
  provider = await import("@/lib/rfq/provider");
  seen = [];
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const precost = { rfq_id: "r1", inputs: {}, resolved: {}, remember: false } as unknown as PrecostRequest;
const company = { settings: {}, profile: {}, prices: [] } as unknown as CompanyRequest;
const created: AdminUserCreateRequest = {
  username: "new-user",
  display_name: "새 계정",
  password: "temporary-1",
  role: "member",
  department: "quality",
};

/** One call of every function, so a new one cannot be added without being checked here. */
function everyCall(): Record<keyof Provider["rfqApi"], () => Promise<unknown>> {
  const { rfqApi } = provider;
  return {
    login: () => rfqApi.login({ username: "quality", password: "secret-123" }),
    logout: () => rfqApi.logout(),
    me: () => rfqApi.me(),
    changePassword: () => rfqApi.changePassword({ current_password: "a", new_password: "b2345678" }),
    adminUsers: () => rfqApi.adminUsers(),
    createUser: () => rfqApi.createUser(created),
    updateUser: () => rfqApi.updateUser("u-2", { disabled: true }),
    resetPassword: () => rfqApi.resetPassword("u-2", { password: "temporary-2" }),
    forceLogout: () => rfqApi.forceLogout("u-2"),
    deleteUser: () => rfqApi.deleteUser("u-2"),
    audit: () => rfqApi.audit(),
    meta: () => rfqApi.meta(),
    rfqs: () => rfqApi.rfqs(),
    upload: () => rfqApi.upload({ kind: "files", files: [new File(["x"], "a.pdf")] }),
    document: () => rfqApi.document("r1"),
    job: () => rfqApi.job("r1"),
    clauses: () => rfqApi.clauses("r1"),
    sentences: () => rfqApi.sentences("r1", "d01", 1),
    source: () => rfqApi.source("r1", "d01"),
    rtm: () => rfqApi.rtm("r1"),
    rtmXlsx: () => rfqApi.rtmXlsx("r1"),
    consistency: () => rfqApi.consistency("r1"),
    precostSetup: () => rfqApi.precostSetup("r1"),
    assignSheetColumns: () => rfqApi.assignSheetColumns("r1", { table: "t", header_row: 0, columns: {} }),
    sheetWords: () => rfqApi.sheetWords(),
    decideSheetWord: () => rfqApi.decideSheetWord({ role: "part", text: "x", action: "approve" }),
    company: () => rfqApi.company(),
    saveCompany: () => rfqApi.saveCompany(company),
    precost: () => rfqApi.precost(precost),
    chat: () => rfqApi.chat("r1", "납기"),
    evaluation: () => rfqApi.evaluation("r1"),
  };
}

describe("every request", () => {
  it("covers every function of the provider", () => {
    expect(Object.keys(everyCall()).sort()).toEqual(Object.keys(provider.rfqApi).sort());
  });

  it("carries the cookie and never says which team is looking", async () => {
    answer(() => apiError(503, "down"));
    for (const call of Object.values(everyCall())) await expect(call()).rejects.toThrow();
    expect(seen).toHaveLength(Object.keys(provider.rfqApi).length);
    for (const { url, init } of seen) {
      expect(init.credentials, url).toBe("include");
      expect(init.cache, url).toBe("no-store");
      expect(new URL(url).search, url).not.toMatch(/team/);
      if (typeof init.body === "string") expect(JSON.parse(init.body), url).not.toHaveProperty("team");
      if (init.body instanceof FormData) expect([...init.body.keys()], url).not.toContain("team");
      expect(JSON.stringify(init.headers ?? {}), url).not.toMatch(/authorization|team/i);
    }
  });

  it("uses the account paths of the design", async () => {
    answer(() => apiError(503, "down"));
    const calls = everyCall();
    const names = ["login", "logout", "me", "changePassword", "adminUsers", "createUser", "updateUser", "resetPassword", "forceLogout", "deleteUser", "audit", "rfqs"] as const;
    for (const name of names) await calls[name]().catch(() => undefined);
    expect(seen.map((s) => `${s.init.method ?? "GET"} ${s.url.replace(API, "")}`)).toEqual([
      "POST /auth/login",
      "POST /auth/logout",
      "GET /auth/me",
      "POST /auth/password",
      "GET /admin/users",
      "POST /admin/users",
      "PATCH /admin/users/u-2",
      "POST /admin/users/u-2/password",
      "POST /admin/users/u-2/logout",
      "DELETE /admin/users/u-2",
      "GET /admin/audit",
      "GET /rfqs",
    ]);
  });
});

describe("a session the server no longer accepts", () => {
  it("is reported once per answer, for data, the PDF and the Excel file alike", async () => {
    const heard: string[] = [];
    provider.setAuthListener((e) => heard.push(`${e.status} ${e.code}`));
    answer(() => apiError(401, "unauthenticated"));
    await expect(provider.rfqApi.clauses("r1")).rejects.toMatchObject({ status: 401 });
    await expect(provider.rfqApi.source("r1", "d01")).rejects.toMatchObject({ status: 401 });
    await expect(provider.rfqApi.rtmXlsx("r1")).rejects.toMatchObject({ status: 401 });
    expect(heard).toEqual(Array(3).fill("401 unauthenticated"));
  });

  it("also hears that the password has to be changed first", async () => {
    const heard: string[] = [];
    provider.setAuthListener((e) => heard.push(e.code));
    answer(() => apiError(403, "password_change_required"));
    await expect(provider.rfqApi.rfqs()).rejects.toMatchObject({ status: 403 });
    expect(heard).toEqual(["password_change_required"]);
  });

  it("does not hear a wrong password, a plain 403, or the check of /auth/me", async () => {
    const heard: string[] = [];
    provider.setAuthListener((e) => heard.push(e.code));
    answer(() => apiError(401, "invalid_credentials"));
    await provider.rfqApi.login({ username: "a", password: "b" }).catch(() => undefined);
    await provider.rfqApi.changePassword({ current_password: "a", new_password: "b2345678" }).catch(() => undefined);
    await provider.rfqApi.me().catch(() => undefined);
    answer(() => apiError(403, "forbidden"));
    await provider.rfqApi.company().catch(() => undefined);
    expect(heard).toEqual([]);
  });

  it("hears a dead session on the password form", async () => {
    const heard: string[] = [];
    provider.setAuthListener((e) => heard.push(e.code));
    answer(() => apiError(401, "unauthenticated"));
    await provider.rfqApi.changePassword({ current_password: "a", new_password: "b2345678" }).catch(() => undefined);
    expect(heard).toEqual(["unauthenticated"]);
  });

  it("ignores a late 401 of the session before this one", async () => {
    const heard: string[] = [];
    provider.setAuthListener((e) => heard.push(e.code));
    let release: (r: Response) => void = () => undefined;
    answer(() => new Promise<Response>((resolve) => (release = resolve)));
    const late = provider.rfqApi.clauses("r1").catch(() => undefined);
    provider.startNewSession();
    release(apiError(401, "unauthenticated"));
    await late;
    expect(heard).toEqual([]);
  });
});

describe("files", () => {
  it("returns the bytes of the PDF and refuses a JSON body in place of the Excel file", async () => {
    answer(() => new Response("%PDF-made-up", { status: 200, headers: { "content-type": "application/pdf" } }));
    expect(new TextDecoder().decode(await provider.rfqApi.source("r1", "d01"))).toBe("%PDF-made-up");
    answer(() => new Response("{}", { status: 200, headers: { "content-type": "application/json" } }));
    await expect(provider.rfqApi.rtmXlsx("r1")).rejects.toMatchObject({ code: "contract_violation" });
  });

  it("shows the reason of a refused file", async () => {
    answer(() => apiError(403, "forbidden", "이 파일은 열 수 없습니다."));
    const refused = provider.rfqApi.source("r1", "d01");
    await expect(refused).rejects.toThrow("이 파일은 열 수 없습니다.");
    await refused.catch((e: unknown) => expect(provider.isForbidden(e)).toBe(true));
  });
});

describe("contract of the viewer", () => {
  it("accepts the viewer and refuses one without its scope", async () => {
    answer(() => json({ data: { viewer: viewer() }, provenance: {} }));
    expect((await provider.rfqApi.me()).data.viewer.username).toBe("quality");
    const partial = { ...viewer(), full_scope: undefined };
    answer(() => json({ data: { viewer: partial }, provenance: {} }));
    await expect(provider.rfqApi.me()).rejects.toMatchObject({ code: "contract_violation" });
  });
});
