/**
 * Contract and policy tests (TASKS S006, S014, CONTRACT-01, INV-8, INV-9):
 * the committed JSON Schema matches the zod contract, every mock API response
 * validates, and restricted teams never receive cost-clause content.
 */
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import type { z } from "zod";
import {
  ChatResponse,
  ClausesResponse,
  CostInputsResponse,
  DocumentResponse,
  EvaluationResponse,
  JobResponse,
  MetaResponse,
  PrecostResponse,
  RtmResponse,
  SentencesResponse,
  UploadResponse,
} from "@/lib/rfq/contract";
import { renderContract } from "@/lib/rfq/contract-json";
import { fixtures } from "@/lib/rfq/mock/data";
import { routeMock } from "@/lib/rfq/mock/router";

const CONTRACT_DIR = path.resolve(__dirname, "../../../contract");
const ID = "sample-nv27-fdlm";

async function call(method: "GET" | "POST", url: string, body?: unknown): Promise<Response> {
  const u = new URL(url, "http://test.local/api/mock/");
  const segments = u.pathname.replace(/^\/api\/mock\//, "").split("/").filter(Boolean);
  const req = new Request(u, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  return routeMock(req, segments);
}

async function expectValid<T extends z.ZodType>(schema: T, res: Response): Promise<z.infer<T>> {
  expect(res.status).toBe(200);
  const parsed = schema.safeParse(await res.json());
  if (!parsed.success) throw new Error(JSON.stringify(parsed.error.issues, null, 2));
  return parsed.data;
}

describe("JSON Schema files in /contract", () => {
  it("match the zod contract exactly (run `pnpm contract:emit` after changing contract.ts)", () => {
    const rendered = renderContract();
    const onDisk = readdirSync(CONTRACT_DIR).filter((f) => f.endsWith(".schema.json"));
    expect(onDisk.map((f) => f.replace(".schema.json", "")).sort()).toEqual(Object.keys(rendered).sort());
    for (const [name, text] of Object.entries(rendered)) {
      expect(readFileSync(path.join(CONTRACT_DIR, `${name}.schema.json`), "utf8"), name).toBe(text);
    }
  });
});

describe("every mock API response satisfies the contract", () => {
  const teams = [null, "quality", "sales", "costing", "unknown-team"];
  const scenarios = ["default", "unverified", "empty"];

  it("meta, document, upload, job, evaluation", async () => {
    await expectValid(MetaResponse, await call("GET", "meta"));
    await expectValid(DocumentResponse, await call("GET", `rfqs/${ID}`));
    await expectValid(UploadResponse, await call("POST", "rfqs", { file_name: "x.doc" }));
    await expectValid(UploadResponse, await call("POST", "rfqs", { file_name: null }));
    await expectValid(JobResponse, await call("GET", `rfqs/${ID}/job?since=${Date.now() - 60_000}`));
    await expectValid(JobResponse, await call("GET", `rfqs/${ID}/job?since=${Date.now()}&scenario=error`));
    await expectValid(EvaluationResponse, await call("GET", `rfqs/${ID}/evaluation`));
  });

  it.each(teams)("clauses, rtm, sentences for team=%s", async (team) => {
    const t = team ? `team=${team}` : "";
    for (const scenario of scenarios) {
      await expectValid(ClausesResponse, await call("GET", `rfqs/${ID}/clauses?${t}&scenario=${scenario}`));
      await expectValid(RtmResponse, await call("GET", `rfqs/${ID}/rtm?${t}&scenario=${scenario}`));
    }
    for (let p = 1; p <= fixtures.document.page_count; p++) {
      await expectValid(SentencesResponse, await call("GET", `rfqs/${ID}/pages/${p}/sentences?${t}`));
    }
  });

  it("cost inputs, precost and chat", async () => {
    await expectValid(CostInputsResponse, await call("GET", `rfqs/${ID}/cost-inputs?team=costing`));
    await expectValid(
      PrecostResponse,
      await call("POST", "precost", {
        team: "costing",
        inputs: { item: "FDLM", min_quantity: 400000, fixed_costs: "227000", variable_cost_per_unit: "9.35", currency: "USD" },
      }),
    );
    for (const question of ["지체상금에 상한이 있나요?", "금형 소유권은?", "날씨 어때?"]) {
      await expectValid(ChatResponse, await call("POST", `rfqs/${ID}/chat`, { team: "sales", question }));
      await expectValid(ChatResponse, await call("POST", `rfqs/${ID}/chat`, { team: "quality", question }));
    }
  });

  it("errors use the error envelope", async () => {
    for (const res of [
      await call("GET", "rfqs/nope"),
      await call("GET", `rfqs/${ID}/clauses?scenario=error`),
      await call("GET", `rfqs/${ID}/cost-inputs?team=quality`),
      await call("POST", "precost", { team: "costing", inputs: { item: "x" } }),
      await call("GET", "does/not/exist"),
    ]) {
      expect(res.status).toBeGreaterThanOrEqual(400);
      const body = (await res.json()) as { error?: { code?: string; message?: string } };
      expect(typeof body.error?.code).toBe("string");
      expect(typeof body.error?.message).toBe("string");
    }
  });

  it("is deterministic: the same request returns the same body", async () => {
    const a = await (await call("GET", `rfqs/${ID}/clauses?team=sales`)).text();
    const b = await (await call("GET", `rfqs/${ID}/clauses?team=sales`)).text();
    expect(a).toBe(b);
  });
});

describe("INV-8: restricted teams never receive cost-clause content", () => {
  const costIds = new Set(fixtures.clauses.cost_sensitive);
  const costTexts = fixtures.clauses.clauses
    .filter((c) => costIds.has(c.section_id))
    .flatMap((c) => [c.title, c.summary.finding.text, ...c.excerpt.map((e) => e.text)]);
  const leakMarkers = ["14.80", "185,000", "42,000", "400,000", ...costTexts];

  function assertNoLeak(body: string, where: string) {
    for (const marker of leakMarkers) expect(body.includes(marker), `${where} leaks "${marker}"`).toBe(false);
  }

  it.each([null, "quality", "rnd", "logistics", "not-a-team"])("team=%s", async (team) => {
    const t = team ? `team=${team}` : "";
    for (const scenario of ["default", "unverified"]) {
      const clauses = await (await call("GET", `rfqs/${ID}/clauses?${t}&scenario=${scenario}`)).text();
      assertNoLeak(clauses, "clauses");
      const parsed = ClausesResponse.parse(JSON.parse(clauses));
      expect(parsed.data.group).toBe("cost_restricted");
      for (const c of parsed.data.clauses.filter((x) => costIds.has(x.section_id))) {
        expect(Object.keys(c).sort()).toEqual(["kind", "section_id"]);
      }
      assertNoLeak(await (await call("GET", `rfqs/${ID}/rtm?${t}&scenario=${scenario}`)).text(), "rtm");
    }
    for (let p = 1; p <= fixtures.document.page_count; p++) {
      assertNoLeak(await (await call("GET", `rfqs/${ID}/pages/${p}/sentences?${t}`)).text(), `sentences p${p}`);
    }
    for (const item of fixtures.chat.items) {
      const res = await call("POST", `rfqs/${ID}/chat`, { team: team ?? undefined, question: item.question });
      const body = await res.text();
      assertNoLeak(body, `chat "${item.question}"`);
      if (item.cost_sensitive) expect(ChatResponse.parse(JSON.parse(body)).data.status).toBe("restricted");
    }
    expect((await call("GET", `rfqs/${ID}/cost-inputs?${t}`)).status).toBe(403);
    const precost = await call("POST", "precost", {
      team: team ?? undefined,
      inputs: { item: "x", min_quantity: 1, fixed_costs: "1", variable_cost_per_unit: "1", currency: "USD" },
    });
    expect(precost.status).toBe(403);
  });

  it("serves the masked PDF to restricted teams and the original to cost-visible teams", async () => {
    const masked = Buffer.from(await (await call("GET", `rfqs/${ID}/source?team=quality`)).arrayBuffer());
    const original = Buffer.from(await (await call("GET", `rfqs/${ID}/source?team=costing`)).arrayBuffer());
    const none = Buffer.from(await (await call("GET", `rfqs/${ID}/source`)).arrayBuffer());
    expect(masked.equals(original)).toBe(false);
    expect(none.equals(masked)).toBe(true);
    expect(masked.includes("14.80")).toBe(false);
    expect(original.includes("14.80")).toBe(true);
    const res = await call("GET", `rfqs/${ID}/source?team=quality`);
    expect(res.headers.get("cache-control")).toContain("no-store");
  });

  it("cost-visible teams see every clause in full", async () => {
    const parsed = ClausesResponse.parse(await (await call("GET", `rfqs/${ID}/clauses?team=costing`)).json());
    expect(parsed.data.group).toBe("cost_visible");
    expect(parsed.data.clauses.every((c) => c.kind === "clause")).toBe(true);
  });
});
