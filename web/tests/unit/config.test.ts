/**
 * Backend-only data access (SPEC §13.2, INV-10): without a base URL every
 * request is a configuration error, never fallback data; with one, requests
 * go to the backend; a response that is not real data is refused.
 * All values here are invented for the test.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

async function freshProvider(env: Record<string, string>) {
  vi.resetModules();
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v);
  return {
    config: await import("@/lib/rfq/config"),
    provider: await import("@/lib/rfq/provider"),
  };
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("backend address", () => {
  it("rejects every request when NEXT_PUBLIC_RFQ_API_BASE is empty, and says no data is substituted", async () => {
    const { config, provider } = await freshProvider({ NEXT_PUBLIC_RFQ_API_BASE: "" });
    expect(() => config.baseUrlOf("document")).toThrow(config.ConfigError);

    // The way screens call it: useResource wraps the call in a promise chain.
    const pending = Promise.resolve().then(() => provider.rfqApi.clauses("x", { team: "sales" }, null));
    await expect(pending).rejects.toBeInstanceOf(config.ConfigError);
    await pending.catch((e: unknown) => expect(provider.describeError(e)).toContain("다른 데이터로 대신하지 않습니다"));
  });

  it("sends requests to the backend with the team only", async () => {
    const { provider } = await freshProvider({ NEXT_PUBLIC_RFQ_API_BASE: "https://backend.example/api/" });
    const seen: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      seen.push(url);
      return json({ error: { code: "x", message: "down" } }, 503);
    });
    await expect(provider.rfqApi.clauses("doc", { team: "sales" }, null)).rejects.toThrow("down");
    await expect(provider.rfqApi.job("r1")).rejects.toThrow("down");
    expect(seen).toEqual(["https://backend.example/api/rfqs/doc/clauses?team=sales", "https://backend.example/api/rfqs/r1/job"]);
  });

  it("uploads the picked files as multipart form data", async () => {
    const { provider } = await freshProvider({ NEXT_PUBLIC_RFQ_API_BASE: "http://api.test" });
    const seen: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      seen.push({ url, init });
      return json({ error: { code: "x", message: "stop" } }, 503);
    });
    const file = new File(["made-up bytes"], "a.pdf", { type: "application/pdf" });
    await expect(provider.rfqApi.upload({ kind: "files", files: [file] })).rejects.toThrow("stop");
    expect(seen[0].url).toBe("http://api.test/rfqs");
    expect(seen[0].init?.body).toBeInstanceOf(FormData);
    expect((seen[0].init?.body as FormData).getAll("files")).toHaveLength(1);
  });
});

describe("only real data is shown (INV-10)", () => {
  const body = (provenance: string) => ({
    data: { group: "cost_restricted", clauses: [] },
    provenance: { analysis: provenance },
  });

  it("accepts a response marked real", async () => {
    const { provider } = await freshProvider({ NEXT_PUBLIC_RFQ_API_BASE: "http://api.test" });
    vi.stubGlobal("fetch", async () => json(body("real")));
    const res = await provider.rfqApi.clauses("doc", { team: null }, null);
    expect(res.data.clauses).toEqual([]);
  });

  it("refuses a response marked mock instead of showing it", async () => {
    const { provider } = await freshProvider({ NEXT_PUBLIC_RFQ_API_BASE: "http://api.test" });
    vi.stubGlobal("fetch", async () => json(body("mock")));
    const pending = provider.rfqApi.clauses("doc", { team: null }, null);
    await expect(pending).rejects.toMatchObject({ code: "not_real_data" });
  });
});
