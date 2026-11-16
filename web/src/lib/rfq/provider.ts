/**
 * Data provider (SPEC §13.1). Screens call only these functions; they never
 * read fixtures and never branch on mock vs. real. Every response is checked
 * against the contract, whichever mode served it (INV-9).
 */
import type { z } from "zod";
import { ConfigError, baseUrlOf, modeOf, type RoutePart } from "./config";
import {
  ChatResponse,
  ClausesResponse,
  CostInputsResponse,
  DocumentResponse,
  ErrorResponse,
  EvaluationResponse,
  JobResponse,
  MetaResponse,
  PrecostResponse,
  RtmResponse,
  SentencesResponse,
  UploadResponse,
  type PrecostInputs,
} from "./contract";

export type RequestCtx = { team: string | null; scenario?: string; speed?: number };

export class ApiError extends Error {
  constructor(
    readonly part: RoutePart,
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function buildUrl(part: RoutePart, path: string, params: Record<string, string | number | null | undefined>): string {
  const base = baseUrlOf(part);
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  }
  const qs = q.toString();
  return `${base}${path}${qs ? `?${qs}` : ""}`;
}

/** Demo-only parameters go to mock parts only; a real backend never sees them. */
function demoParams(part: RoutePart, ctx: RequestCtx): Record<string, string | undefined> {
  if (modeOf(part) !== "mock") return {};
  return { scenario: ctx.scenario && ctx.scenario !== "default" ? ctx.scenario : undefined };
}

async function request<T extends z.ZodType>(
  part: RoutePart,
  schema: T,
  url: string,
  init: RequestInit = {},
): Promise<z.infer<T>> {
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store", ...init });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(part, 0, "network", "서버에 연결하지 못했습니다.");
  }
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const parsed = ErrorResponse.safeParse(body);
    const message = parsed.success ? parsed.data.error.message : `요청이 실패했습니다 (${res.status}).`;
    throw new ApiError(part, res.status, parsed.success ? parsed.data.error.code : "http_error", message);
  }
  const checked = schema.safeParse(body);
  if (!checked.success) {
    throw new ApiError(part, res.status, "contract_violation", "응답이 계약(contract/)과 맞지 않습니다.");
  }
  return checked.data;
}

const post = (body: unknown, signal?: AbortSignal): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
  signal,
});

export const rfqApi = {
  meta: (signal?: AbortSignal) => request("document", MetaResponse, buildUrl("document", "/meta", {}), { signal }),

  /** Sends only the file name. The file itself is never transmitted in the demo shell. */
  upload: (fileName: string | null) =>
    request("document", UploadResponse, buildUrl("document", "/rfqs", {}), post({ file_name: fileName })),

  document: (id: string, signal?: AbortSignal) =>
    request("document", DocumentResponse, buildUrl("document", `/rfqs/${encodeURIComponent(id)}`, {}), { signal }),

  job: (id: string, since: number, ctx: RequestCtx, signal?: AbortSignal) =>
    request(
      "document",
      JobResponse,
      buildUrl("document", `/rfqs/${encodeURIComponent(id)}/job`, {
        since,
        ...demoParams("document", ctx),
        speed: modeOf("document") === "mock" ? ctx.speed : undefined,
      }),
      { signal },
    ),

  clauses: (id: string, ctx: RequestCtx, department: string | null, signal?: AbortSignal) =>
    request(
      "analysis",
      ClausesResponse,
      buildUrl("analysis", `/rfqs/${encodeURIComponent(id)}/clauses`, {
        team: ctx.team,
        department,
        ...demoParams("analysis", ctx),
      }),
      { signal },
    ),

  sentences: (id: string, page: number, ctx: RequestCtx, signal?: AbortSignal) =>
    request(
      "source",
      SentencesResponse,
      buildUrl("source", `/rfqs/${encodeURIComponent(id)}/pages/${page}/sentences`, { team: ctx.team }),
      { signal },
    ),

  /** URL of the canonical PDF for this team (masked for the restricted group). */
  sourceUrl: (id: string, ctx: RequestCtx) =>
    buildUrl("source", `/rfqs/${encodeURIComponent(id)}/source`, { team: ctx.team }),

  rtm: (id: string, ctx: RequestCtx, signal?: AbortSignal) =>
    request(
      "analysis",
      RtmResponse,
      buildUrl("analysis", `/rfqs/${encodeURIComponent(id)}/rtm`, { team: ctx.team, ...demoParams("analysis", ctx) }),
      { signal },
    ),

  costInputs: (id: string, ctx: RequestCtx, signal?: AbortSignal) =>
    request(
      "cost_inputs",
      CostInputsResponse,
      buildUrl("cost_inputs", `/rfqs/${encodeURIComponent(id)}/cost-inputs`, { team: ctx.team }),
      { signal },
    ),

  precost: (inputs: PrecostInputs, ctx: RequestCtx) =>
    request("precost", PrecostResponse, buildUrl("precost", "/precost", {}), post({ team: ctx.team ?? undefined, inputs })),

  chat: (id: string, question: string, ctx: RequestCtx) =>
    request(
      "chat",
      ChatResponse,
      buildUrl("chat", `/rfqs/${encodeURIComponent(id)}/chat`, {}),
      post({ team: ctx.team ?? undefined, question }),
    ),

  evaluation: (id: string, signal?: AbortSignal) =>
    request("evaluation", EvaluationResponse, buildUrl("evaluation", `/rfqs/${encodeURIComponent(id)}/evaluation`, {}), {
      signal,
    }),
};

/** Human-readable message for any failure, including configuration errors. */
export function describeError(e: unknown): string {
  if (e instanceof ApiError || e instanceof ConfigError) return e.message;
  if (e instanceof Error) return e.message;
  return "알 수 없는 오류가 발생했습니다.";
}
