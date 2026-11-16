/**
 * Per-part modes (SPEC §13.2, INV-10): an `http` part without a base URL is an
 * error for that part only, never a silent fallback to mock; when every part
 * is `http` the mock API is off.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

const PARTS = ["DOCUMENT", "ANALYSIS", "SOURCE", "COST_INPUTS", "PRECOST", "CHAT", "EVALUATION"];

async function freshConfig(env: Record<string, string>) {
  vi.resetModules();
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v);
  return {
    config: await import("@/lib/rfq/config"),
    provider: await import("@/lib/rfq/provider"),
  };
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("per-part mode", () => {
  it("an http part without a base URL rejects; other parts stay mock", async () => {
    const { config, provider } = await freshConfig({ NEXT_PUBLIC_RFQ_MODE_ANALYSIS: "http", NEXT_PUBLIC_RFQ_API_BASE: "" });
    expect(config.modeOf("analysis")).toBe("http");
    expect(config.modeOf("document")).toBe("mock");
    expect(config.baseUrlOf("document")).toBe("/api/mock");
    expect(() => config.baseUrlOf("analysis")).toThrow(config.ConfigError);

    // The way screens call it: useResource wraps the call in a promise chain.
    const pending = Promise.resolve().then(() => provider.rfqApi.clauses("x", { team: "sales" }, null));
    await expect(pending).rejects.toBeInstanceOf(config.ConfigError);
    await pending.catch((e: unknown) => expect(provider.describeError(e)).toContain("목업으로 대신하지 않습니다"));
  });

  it("an http part with a base URL goes to the backend and drops demo parameters", async () => {
    const { provider } = await freshConfig({
      NEXT_PUBLIC_RFQ_MODE_ANALYSIS: "http",
      NEXT_PUBLIC_RFQ_API_BASE: "https://backend.example/api/",
    });
    const seen: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      seen.push(url);
      return new Response(JSON.stringify({ error: { code: "x", message: "down" } }), { status: 503 });
    });
    await expect(provider.rfqApi.clauses("doc", { team: "sales", scenario: "error" }, null)).rejects.toThrow("down");
    expect(seen[0]).toBe("https://backend.example/api/rfqs/doc/clauses?team=sales");
  });

  it("rejects an unknown mode value instead of guessing", async () => {
    const { config } = await freshConfig({ NEXT_PUBLIC_RFQ_MODE_CHAT: "real" });
    expect(() => config.modeOf("chat")).toThrow(config.ConfigError);
  });

  it("turns the mock API off when every part is http", async () => {
    const { config } = await freshConfig(Object.fromEntries(PARTS.map((p) => [`NEXT_PUBLIC_RFQ_MODE_${p}`, "http"])));
    expect(config.allHttp()).toBe(true);
    const { routeMock } = await import("@/lib/rfq/mock/router");
    const res = await routeMock(new Request("http://t/api/mock/meta"), ["meta"]);
    expect(res.status).toBe(404);
    expect(((await res.json()) as { error: { code: string } }).error.code).toBe("mock_disabled");
  });
});

describe("department filter does not reveal hidden clauses' departments", () => {
  it("a restricted team sees the same masked rows under every department", async () => {
    vi.resetModules();
    const { getClauses, fixtures } = await import("@/lib/rfq/mock/data");
    const ids = (dept: string | null) =>
      getClauses(fixtures.document.id, "quality", dept, "default")
        .data.clauses.filter((c) => c.kind === "masked")
        .map((c) => c.section_id);
    const all = ids(null);
    expect(all.length).toBeGreaterThan(0);
    for (const d of fixtures.meta.departments) expect(ids(d.id)).toEqual(all);
  });
});
