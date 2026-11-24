/**
 * Data provider (SPEC §13.1). Screens call only these functions. Every
 * response comes from the backend, is checked against the contract (INV-9),
 * and must say it is real data: anything else is an error (INV-10).
 *
 * Who is asking is the session cookie and nothing else (docs/auth-rbac.md §1):
 * every request sends it, and no request names a team or a role to be trusted.
 */
import type { z } from "zod";
import { ConfigError, baseUrlOf, type RoutePart } from "./config";
import {
  AdminUserResponse,
  AdminUsersResponse,
  AuditResponse,
  ChatResponse,
  ClausesResponse,
  CompanyResponse,
  ConsistencyResponse,
  DocumentResponse,
  ErrorResponse,
  EvaluationResponse,
  JobResponse,
  MetaResponse,
  OkResponse,
  PrecostResponse,
  PrecostSetupResponse,
  RfqListResponse,
  RtmResponse,
  SentencesResponse,
  SheetColumnsResponse,
  SheetWordsResponse,
  UploadResponse,
  ViewerResponse,
  type AdminPasswordRequest,
  type AdminUserCreateRequest,
  type AdminUserPatchRequest,
  type CompanyRequest,
  type LoginRequest,
  type PasswordChangeRequest,
  type PrecostRequest,
  type SheetColumnsRequest,
  type SheetWordRequest,
} from "./contract";

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

/** True for a 403: the account may not see this. Screens show a lock, not an error. */
export function isForbidden(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403;
}

// ── the session hook ────────────────────────────────────────────────────────
type AuthListener = (e: ApiError) => void;
let authListener: AuthListener | null = null;
let sessionNumber = 0;

/** The one place that hears about a session the server no longer accepts (the session store). */
export function setAuthListener(listener: AuthListener | null): void {
  authListener = listener;
}

/** Called when another session begins: answers to requests of the one before are not about it. */
export function startNewSession(): void {
  sessionNumber += 1;
}

/**
 * Which failures are about the session. "session": any 401. "credentials": the call itself sends a
 * password, so only the gate's own `unauthenticated` counts and a wrong password does not. "none":
 * the caller decides (the check of /auth/me).
 */
type AuthCheck = "session" | "credentials" | "none";

function reportAuth(e: ApiError, check: AuthCheck, startedIn: number): void {
  if (check === "none" || startedIn !== sessionNumber) return;
  const isLost = e.status === 401 && (check === "session" || e.code === "unauthenticated");
  if (isLost || (e.status === 403 && e.code === "password_change_required")) authListener?.(e);
}

/** No query string: who is asking is in the cookie, and filters work on what the server already sent. */
const endpoint = (part: RoutePart, path: string): string => `${baseUrlOf(part)}${path}`;

/** A response whose provenance names anything but real data is refused (INV-10). */
function isRealData(body: unknown): boolean {
  if (typeof body !== "object" || body === null || !("provenance" in body)) return true;
  const provenance = (body as { provenance: unknown }).provenance;
  if (typeof provenance !== "object" || provenance === null) return true;
  return Object.values(provenance).every((v) => v === "real");
}

/** One request with the session cookie; a failure becomes an ApiError with the server's message. */
async function send(part: RoutePart, url: string, init: RequestInit = {}, check: AuthCheck = "session"): Promise<Response> {
  const startedIn = sessionNumber;
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store", ...init, credentials: "include" });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(part, 0, "network", "서버에 연결하지 못했습니다.");
  }
  if (res.ok) return res;
  const parsed = ErrorResponse.safeParse(await res.json().catch(() => null));
  const message = parsed.success ? parsed.data.error.message : `요청이 실패했습니다 (${res.status}).`;
  const error = new ApiError(part, res.status, parsed.success ? parsed.data.error.code : "http_error", message);
  reportAuth(error, check, startedIn);
  throw error;
}

async function request<T extends z.ZodType>(
  part: RoutePart,
  schema: T,
  url: string,
  init: RequestInit = {},
  check: AuthCheck = "session",
): Promise<z.infer<T>> {
  const res = await send(part, url, init, check);
  const checked = schema.safeParse(await res.json().catch(() => null));
  if (!checked.success) {
    throw new ApiError(part, res.status, "contract_violation", "응답이 계약(contract/)과 맞지 않습니다.");
  }
  if (!isRealData(checked.data)) {
    throw new ApiError(part, res.status, "not_real_data", "백엔드가 실제 데이터가 아닌 응답을 보냈습니다. 보여주지 않습니다.");
  }
  return checked.data;
}

/** A file answer. Another content type (for example an error page) is refused, not saved as the file. */
async function requestFile(part: RoutePart, type: string, url: string, init: RequestInit = {}): Promise<Response> {
  const res = await send(part, url, init);
  if (!(res.headers.get("content-type") ?? "").startsWith(type)) {
    throw new ApiError(part, res.status, "contract_violation", "백엔드가 파일이 아닌 응답을 보냈습니다.");
  }
  return res;
}

const post = (body: unknown, signal?: AbortSignal): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
  signal,
});

const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

const account = (id: string, action = "") => endpoint("admin", `/admin/users/${encodeURIComponent(id)}${action}`);
const rfq = (id: string) => `/rfqs/${encodeURIComponent(id)}`;
const file = (id: string, fileId: string) => `${rfq(id)}/files/${encodeURIComponent(fileId)}`;

/** What to analyse: files picked by the user. */
export type UploadSource = { kind: "files"; files: File[] };

export const rfqApi = {
  // ── session (docs/auth-rbac.md §3) ──
  login: (body: LoginRequest) =>
    request("auth", ViewerResponse, endpoint("auth", "/auth/login"), post(body), "credentials"),

  logout: () => request("auth", OkResponse, endpoint("auth", "/auth/logout"), { method: "POST" }, "none"),

  me: (signal?: AbortSignal) =>
    request("auth", ViewerResponse, endpoint("auth", "/auth/me"), { signal }, "none"),

  /** Ends the account's other sessions; the answer comes with the cookie of a new one. */
  changePassword: (body: PasswordChangeRequest) =>
    request("auth", ViewerResponse, endpoint("auth", "/auth/password"), post(body), "credentials"),

  // ── accounts (administrators) ──
  adminUsers: (signal?: AbortSignal) =>
    request("admin", AdminUsersResponse, endpoint("admin", "/admin/users"), { signal }),

  createUser: (body: AdminUserCreateRequest) =>
    request("admin", AdminUserResponse, endpoint("admin", "/admin/users"), post(body)),

  updateUser: (id: string, body: AdminUserPatchRequest) =>
    request("admin", AdminUserResponse, account(id), { ...post(body), method: "PATCH" }),

  resetPassword: (id: string, body: AdminPasswordRequest) =>
    request("admin", AdminUserResponse, account(id, "/password"), post(body)),

  forceLogout: (id: string) => request("admin", OkResponse, account(id, "/logout"), { method: "POST" }),

  deleteUser: (id: string) => request("admin", OkResponse, account(id), { method: "DELETE" }),

  audit: (signal?: AbortSignal) =>
    request("admin", AuditResponse, endpoint("admin", "/admin/audit"), { signal }),

  // ── packages ──
  meta: (signal?: AbortSignal) => request("document", MetaResponse, endpoint("document", "/meta"), { signal }),

  /** The packages on the server, named as far as this account may see them. */
  rfqs: (signal?: AbortSignal) =>
    request("document", RfqListResponse, endpoint("document", "/rfqs"), { signal }),

  /**
   * The backend receives the files as multipart/form-data (field `files`). The
   * browser sets the multipart boundary, so no content-type is written here.
   */
  upload: (source: UploadSource) => {
    const url = endpoint("document", "/rfqs");
    const form = new FormData();
    for (const f of source.files) form.append("files", f, f.name);
    return request("document", UploadResponse, url, { method: "POST", body: form });
  },

  document: (id: string, signal?: AbortSignal) =>
    request("document", DocumentResponse, endpoint("document", rfq(id)), { signal }),

  job: (id: string, signal?: AbortSignal) =>
    request("document", JobResponse, endpoint("document", `${rfq(id)}/job`), { signal }),

  /** Every clause of the RFQ: what the account may see, and a lock row for the rest. */
  clauses: (id: string, signal?: AbortSignal) =>
    request("analysis", ClausesResponse, endpoint("analysis", `${rfq(id)}/clauses`), { signal }),

  sentences: (id: string, fileId: string, page: number, signal?: AbortSignal) =>
    request(
      "source",
      SentencesResponse,
      endpoint("source", `${file(id, fileId)}/pages/${page}/sentences`),
      { signal },
    ),

  /** Bytes of one file's PDF as the server made it for this account (out-of-scope parts are grey). */
  source: async (id: string, fileId: string, signal?: AbortSignal): Promise<Uint8Array> => {
    const url = endpoint("source", `${file(id, fileId)}/source`);
    const res = await requestFile("source", "application/pdf", url, { signal });
    return new Uint8Array(await res.arrayBuffer());
  },

  rtm: (id: string, signal?: AbortSignal) =>
    request("analysis", RtmResponse, endpoint("analysis", `${rfq(id)}/rtm`), { signal }),

  /** Server-built Excel RTM for this account's scope. */
  rtmXlsx: async (id: string): Promise<Blob> => {
    const res = await requestFile("analysis", XLSX, endpoint("analysis", `${rfq(id)}/rtm.xlsx`));
    return res.blob();
  },

  consistency: (id: string, signal?: AbortSignal) =>
    request("consistency", ConsistencyResponse, endpoint("consistency", `${rfq(id)}/consistency`), { signal }),

  /** Steps ①–⑤ for one RFQ: type, knockout questions, cited cost inputs, suggested cost items. */
  precostSetup: (id: string, signal?: AbortSignal) =>
    request("cost_inputs", PrecostSetupResponse, endpoint("cost_inputs", `${rfq(id)}/precost-setup`), { signal }),

  /** Read one price-sheet table by columns assigned by hand; the same header is read this way from now on. */
  assignSheetColumns: (id: string, body: SheetColumnsRequest) =>
    request(
      "cost_inputs",
      SheetColumnsResponse,
      endpoint("cost_inputs", `${rfq(id)}/price-sheet/columns`),
      post(body),
    ),

  /** Header words assigned by hand: candidates for the price-sheet dictionary, and the approved ones. */
  sheetWords: (signal?: AbortSignal) =>
    request("precost", SheetWordsResponse, endpoint("precost", "/price-sheet/words"), { signal }),

  decideSheetWord: (body: SheetWordRequest) =>
    request("precost", SheetWordsResponse, endpoint("precost", "/price-sheet/words"), post(body)),

  /** Company settings: price table, knockout profile and rates (cost-visible accounts only). */
  company: (signal?: AbortSignal) =>
    request("precost", CompanyResponse, endpoint("precost", "/company"), { signal }),

  /** Replaces the whole record (administrators). A price row with `id: null` is new; stored answers are kept. */
  saveCompany: (body: CompanyRequest) =>
    request("precost", CompanyResponse, endpoint("precost", "/company"), { ...post(body), method: "PUT" }),

  precost: (body: PrecostRequest) =>
    request("precost", PrecostResponse, endpoint("precost", "/precost"), post(body)),

  chat: (id: string, question: string) =>
    request("chat", ChatResponse, endpoint("chat", `${rfq(id)}/chat`), post({ question })),

  /** Rows about clauses outside the account's scope are left out by the server (INV-8). */
  evaluation: (id: string, signal?: AbortSignal) =>
    request("evaluation", EvaluationResponse, endpoint("evaluation", `${rfq(id)}/evaluation`), { signal }),
};

/** Human-readable message for any failure, including configuration errors. */
export function describeError(e: unknown): string {
  if (e instanceof ApiError || e instanceof ConfigError) return e.message;
  if (e instanceof Error) return e.message;
  return "알 수 없는 오류가 발생했습니다.";
}
