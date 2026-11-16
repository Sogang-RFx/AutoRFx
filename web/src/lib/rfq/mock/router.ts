/**
 * Mock API router (SPEC §13.3). One entry point so the route file stays a
 * one-liner and unit tests can call the router without a running server.
 */
import { ZodError } from "zod";
import { allHttp } from "../config";
import { ChatRequest, PrecostRequest, UploadRequest } from "../contract";
import {
  MockError,
  assertCostVisible,
  chat,
  getClauses,
  getCostInputs,
  getDocument,
  getEvaluation,
  getJob,
  getMeta,
  getRtm,
  getSentences,
  parseScenario,
  sourcePdf,
  upload,
} from "./data";
import { PrecostInputError, computePrecost } from "./precost";

const NO_STORE = { "cache-control": "private, no-store", vary: "*" } as const;

function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: NO_STORE });
}

function error(status: number, code: string, message: string): Response {
  return json({ error: { code, message } }, status);
}

async function body(req: Request): Promise<unknown> {
  try {
    return await req.json();
  } catch {
    throw new MockError(400, "invalid_json", "요청 본문이 JSON이 아닙니다.");
  }
}

type Handler = (req: Request, params: string[], q: URLSearchParams) => Promise<Response> | Response;
interface RouteDef {
  method: "GET" | "POST";
  pattern: string[]; // ":x" captures
  handler: Handler;
}

const routes: RouteDef[] = [
  { method: "GET", pattern: ["meta"], handler: () => json(getMeta()) },
  {
    method: "POST",
    pattern: ["rfqs"],
    handler: async (req) => json(upload(UploadRequest.parse(await body(req)).file_name)),
  },
  { method: "GET", pattern: ["rfqs", ":id"], handler: (_r, [id]) => json(getDocument(id)) },
  {
    method: "GET",
    pattern: ["rfqs", ":id", "job"],
    handler: (_r, [id], q) =>
      json(
        getJob(id, Number(q.get("since") ?? Date.now()), Number(q.get("speed") ?? 1), parseScenario(q.get("scenario"))),
      ),
  },
  {
    method: "GET",
    pattern: ["rfqs", ":id", "clauses"],
    handler: (_r, [id], q) =>
      json(getClauses(id, q.get("team"), q.get("department"), parseScenario(q.get("scenario")))),
  },
  {
    method: "GET",
    pattern: ["rfqs", ":id", "source"],
    handler: (_r, [id], q) =>
      new Response(new Uint8Array(sourcePdf(id, q.get("team"))), {
        headers: { ...NO_STORE, "content-type": "application/pdf", "content-disposition": "inline" },
      }),
  },
  {
    method: "GET",
    pattern: ["rfqs", ":id", "pages", ":n", "sentences"],
    handler: (_r, [id, n], q) => json(getSentences(id, q.get("team"), Number(n))),
  },
  {
    method: "GET",
    pattern: ["rfqs", ":id", "rtm"],
    handler: (_r, [id], q) => json(getRtm(id, q.get("team"), parseScenario(q.get("scenario")))),
  },
  {
    method: "GET",
    pattern: ["rfqs", ":id", "cost-inputs"],
    handler: (_r, [id], q) => json(getCostInputs(id, q.get("team"))),
  },
  {
    method: "POST",
    pattern: ["rfqs", ":id", "chat"],
    handler: async (req, [id]) => {
      const parsed = ChatRequest.parse(await body(req));
      return json(chat(id, parsed.team ?? null, parsed.question));
    },
  },
  {
    method: "POST",
    pattern: ["precost"],
    handler: async (req) => {
      const parsed = PrecostRequest.parse(await body(req));
      assertCostVisible(parsed.team ?? null);
      const result = computePrecost(parsed.inputs);
      return json({
        data: {
          inputs: parsed.inputs,
          ...result,
          decision: {
            status: "baseline_undefined",
            note: "Go/No-Go 판정식은 도메인 팀 정의서를 받은 뒤 정한다(OQ-3). 지금은 수치만 보여준다.",
          },
        },
        provenance: { precost: "mock" },
      });
    },
  },
  { method: "GET", pattern: ["rfqs", ":id", "evaluation"], handler: (_r, [id]) => json(getEvaluation(id)) },
];

function match(pattern: string[], segments: string[]): string[] | null {
  if (pattern.length !== segments.length) return null;
  const captured: string[] = [];
  for (let i = 0; i < pattern.length; i++) {
    if (pattern[i].startsWith(":")) captured.push(decodeURIComponent(segments[i]));
    else if (pattern[i] !== segments[i]) return null;
  }
  return captured;
}

export async function routeMock(req: Request, segments: string[]): Promise<Response> {
  if (allHttp()) return error(404, "mock_disabled", "모든 부분이 http 모드라 목업 API가 꺼져 있습니다.");
  const method = req.method === "POST" ? "POST" : "GET";
  const q = new URL(req.url).searchParams;
  for (const route of routes) {
    const params = match(route.pattern, segments);
    if (!params || route.method !== method) continue;
    try {
      return await route.handler(req, params, q);
    } catch (e) {
      if (e instanceof MockError) return error(e.status, e.code, e.message);
      if (e instanceof ZodError) return error(400, "invalid_request", e.issues.map((i) => i.message).join("; "));
      if (e instanceof PrecostInputError) return error(400, "invalid_precost_input", e.message);
      return error(500, "internal", "목업 API 내부 오류");
    }
  }
  return error(404, "not_found", `목업 API에 '${method} /${segments.join("/")}' 경로가 없습니다.`);
}
