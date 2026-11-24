/**
 * Data provider (SPEC §13.1). Screens call only these functions. Every
 * response comes from the backend, is checked against the contract (INV-9),
 * and must say it is real data: anything else is an error (INV-10).
 */
import type { z } from "zod";
import { ConfigError, baseUrlOf, type RoutePart } from "./config";
import {
  ChatResponse,
  ClausesResponse,
  CompanyResponse,
  ConsistencyResponse,
  DocumentResponse,
  ErrorResponse,
  EvaluationResponse,
  JobResponse,
  MetaResponse,
  OrgDepartmentsResponse,
  PrecostResponse,
  PrecostSetupResponse,
  RtmResponse,
  SentencesResponse,
  SheetColumnsResponse,
  SheetSuggestResponse,
  SheetWordsResponse,
  UploadResponse,
  type CompanyRequest,
  type OrgDepartmentsRequest,
  type PrecostRequest,
  type SheetColumnsRequest,
  type SheetSuggestRequest,
  type SheetWordRequest,
} from "./contract";

export type RequestCtx = { team: string | null };

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

/** True for a 403: the team may not see this. Screens show a lock, not an error. */
export function isForbidden(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403;
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

/** A response whose provenance names anything but real data is refused (INV-10). */
function isRealData(body: unknown): boolean {
  if (typeof body !== "object" || body === null || !("provenance" in body)) return true;
  const provenance = (body as { provenance: unknown }).provenance;
  if (typeof provenance !== "object" || provenance === null) return true;
  return Object.values(provenance).every((v) => v === "real");
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
  if (!isRealData(checked.data)) {
    throw new ApiError(part, res.status, "not_real_data", "백엔드가 실제 데이터가 아닌 응답을 보냈습니다. 보여주지 않습니다.");
  }
  return checked.data;
}

const post = (body: unknown, signal?: AbortSignal): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
  signal,
});

const rfq = (id: string) => `/rfqs/${encodeURIComponent(id)}`;
const file = (id: string, fileId: string) => `${rfq(id)}/files/${encodeURIComponent(fileId)}`;

/** What to analyse: files picked by the user. Nothing on the server can be opened by id. */
export type UploadSource = { kind: "files"; files: File[] };

export const rfqApi = {
  meta: (signal?: AbortSignal) => request("document", MetaResponse, buildUrl("document", "/meta", {}), { signal }),

  /** Company departments (SPEC US10). Saving replaces the whole list; an empty list restores the defaults. */
  orgDepartments: (signal?: AbortSignal) =>
    request("document", OrgDepartmentsResponse, buildUrl("document", "/org/departments", {}), { signal }),

  saveOrgDepartments: (body: OrgDepartmentsRequest) =>
    request("document", OrgDepartmentsResponse, buildUrl("document", "/org/departments", {}), {
      ...post(body),
      method: "PUT",
    }),

  /**
   * The backend receives the files as multipart/form-data (field `files`). The
   * browser sets the multipart boundary, so no content-type is written here.
   */
  upload: (source: UploadSource) => {
    const url = buildUrl("document", "/rfqs", {});
    const form = new FormData();
    for (const f of source.files) form.append("files", f, f.name);
    return request("document", UploadResponse, url, { method: "POST", body: form });
  },

  document: (id: string, signal?: AbortSignal) =>
    request("document", DocumentResponse, buildUrl("document", rfq(id), {}), { signal }),

  job: (id: string, signal?: AbortSignal) =>
    request("document", JobResponse, buildUrl("document", `${rfq(id)}/job`, {}), { signal }),

  clauses: (id: string, ctx: RequestCtx, department: string | null, signal?: AbortSignal) =>
    request(
      "analysis",
      ClausesResponse,
      buildUrl("analysis", `${rfq(id)}/clauses`, { team: ctx.team, department }),
      { signal },
    ),

  sentences: (id: string, fileId: string, page: number, ctx: RequestCtx, signal?: AbortSignal) =>
    request(
      "source",
      SentencesResponse,
      buildUrl("source", `${file(id, fileId)}/pages/${page}/sentences`, { team: ctx.team }),
      { signal },
    ),

  /** URL of one file's canonical PDF for this team (cost text redacted for the restricted group). */
  sourceUrl: (id: string, fileId: string, ctx: RequestCtx) =>
    buildUrl("source", `${file(id, fileId)}/source`, { team: ctx.team }),

  rtm: (id: string, ctx: RequestCtx, signal?: AbortSignal) =>
    request("analysis", RtmResponse, buildUrl("analysis", `${rfq(id)}/rtm`, { team: ctx.team }), { signal }),

  /** Server-built Excel RTM, filtered for the team. */
  rtmXlsxUrl: (id: string, ctx: RequestCtx): string =>
    buildUrl("analysis", `${rfq(id)}/rtm.xlsx`, { team: ctx.team }),

  consistency: (id: string, ctx: RequestCtx, signal?: AbortSignal) =>
    request(
      "consistency",
      ConsistencyResponse,
      buildUrl("consistency", `${rfq(id)}/consistency`, { team: ctx.team }),
      { signal },
    ),

  /** Steps ①–⑤ for one RFQ: type, knockout questions, cited cost inputs, suggested cost items. */
  precostSetup: (id: string, ctx: RequestCtx, signal?: AbortSignal) =>
    request(
      "cost_inputs",
      PrecostSetupResponse,
      buildUrl("cost_inputs", `${rfq(id)}/precost-setup`, { team: ctx.team }),
      { signal },
    ),

  /** Read one price-sheet table by columns assigned by hand; the same header is read this way from now on. */
  assignSheetColumns: (id: string, body: SheetColumnsRequest, ctx: RequestCtx) =>
    request(
      "cost_inputs",
      SheetColumnsResponse,
      buildUrl("cost_inputs", `${rfq(id)}/price-sheet/columns`, { team: ctx.team }),
      post(body),
    ),

  /** An AI suggestion for the columns of one table (positions only); the user accepts it with assignSheetColumns. */
  suggestSheetColumns: (id: string, body: SheetSuggestRequest, ctx: RequestCtx) =>
    request(
      "cost_inputs",
      SheetSuggestResponse,
      buildUrl("cost_inputs", `${rfq(id)}/price-sheet/suggest`, { team: ctx.team }),
      post(body),
    ),

  /** Header words assigned by hand: candidates for the price-sheet dictionary, and the approved ones. */
  sheetWords: (ctx: RequestCtx, signal?: AbortSignal) =>
    request("precost", SheetWordsResponse, buildUrl("precost", "/price-sheet/words", { team: ctx.team }), { signal }),

  decideSheetWord: (body: SheetWordRequest, ctx: RequestCtx) =>
    request("precost", SheetWordsResponse, buildUrl("precost", "/price-sheet/words", { team: ctx.team }), post(body)),

  /** Company settings: price table, knockout profile and rates (cost-visible teams only). */
  company: (ctx: RequestCtx, signal?: AbortSignal) =>
    request("precost", CompanyResponse, buildUrl("precost", "/company", { team: ctx.team }), { signal }),

  /** Replaces the whole record. A price row with `id: null` is new; stored answers are kept. */
  saveCompany: (body: CompanyRequest, ctx: RequestCtx) =>
    request("precost", CompanyResponse, buildUrl("precost", "/company", { team: ctx.team }), {
      ...post(body),
      method: "PUT",
    }),

  precost: (body: Omit<PrecostRequest, "team">, ctx: RequestCtx) =>
    request("precost", PrecostResponse, buildUrl("precost", "/precost", {}), post({ ...body, team: ctx.team ?? undefined })),

  chat: (id: string, question: string, ctx: RequestCtx) =>
    request("chat", ChatResponse, buildUrl("chat", `${rfq(id)}/chat`, {}), post({ team: ctx.team ?? undefined, question })),

  /** The team decides which error rows are shown: rows about hidden clauses are left out (INV-8). */
  evaluation: (id: string, ctx: RequestCtx, signal?: AbortSignal) =>
    request(
      "evaluation",
      EvaluationResponse,
      buildUrl("evaluation", `${rfq(id)}/evaluation`, { team: ctx.team }),
      { signal },
    ),
};

/** Human-readable message for any failure, including configuration errors. */
export function describeError(e: unknown): string {
  if (e instanceof ApiError || e instanceof ConfigError) return e.message;
  if (e instanceof Error) return e.message;
  return "알 수 없는 오류가 발생했습니다.";
}
