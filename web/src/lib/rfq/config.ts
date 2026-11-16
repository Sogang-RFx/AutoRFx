/**
 * Per-part data mode (SPEC §13.2). Each part is `mock` (in-app mock API route)
 * or `http` (backend API, after the team meeting). There is no silent fallback:
 * an `http` part without a base URL is a configuration error for that part.
 *
 * NEXT_PUBLIC_* values are inlined at build time only when referenced
 * literally, so every variable is spelled out below.
 */
import type { Part } from "./contract";

export type Mode = "mock" | "http";

/** Endpoint groups. Routing uses the part that owns the endpoint. */
export type RoutePart = "document" | "analysis" | "source" | "cost_inputs" | "precost" | "chat" | "evaluation";

const RAW_PART_MODES: Record<RoutePart, string | undefined> = {
  document: process.env.NEXT_PUBLIC_RFQ_MODE_DOCUMENT,
  analysis: process.env.NEXT_PUBLIC_RFQ_MODE_ANALYSIS,
  source: process.env.NEXT_PUBLIC_RFQ_MODE_SOURCE,
  cost_inputs: process.env.NEXT_PUBLIC_RFQ_MODE_COST_INPUTS,
  precost: process.env.NEXT_PUBLIC_RFQ_MODE_PRECOST,
  chat: process.env.NEXT_PUBLIC_RFQ_MODE_CHAT,
  evaluation: process.env.NEXT_PUBLIC_RFQ_MODE_EVALUATION,
};
const RAW_DEFAULT = process.env.NEXT_PUBLIC_RFQ_MODE;
const API_BASE = process.env.NEXT_PUBLIC_RFQ_API_BASE ?? "";
const IS_PRODUCTION = process.env.NODE_ENV === "production";

export const ROUTE_PARTS = Object.keys(RAW_PART_MODES) as RoutePart[];

/** Which contract parts each endpoint group carries (for the mock badge count). */
export const ROUTE_PART_MEMBERS: Record<RoutePart, Part[]> = {
  document: ["document"],
  analysis: ["parsing", "analysis", "citation", "translation"],
  source: ["source"],
  cost_inputs: ["cost_inputs"],
  precost: ["precost"],
  chat: ["chat"],
  evaluation: ["evaluation"],
};

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

function parseMode(value: string | undefined, label: string): Mode | undefined {
  if (value === undefined || value === "") return undefined;
  if (value === "mock" || value === "http") return value;
  throw new ConfigError(`${label}의 값 "${value}"는 mock 또는 http여야 합니다.`);
}

/** Default mode: `mock` in development; production builds must state it. */
export function defaultMode(): Mode {
  const parsed = parseMode(RAW_DEFAULT, "NEXT_PUBLIC_RFQ_MODE");
  if (parsed) return parsed;
  if (IS_PRODUCTION) {
    throw new ConfigError("배포용 실행에는 NEXT_PUBLIC_RFQ_MODE(mock 또는 http)를 지정해야 합니다.");
  }
  return "mock";
}

export function modeOf(part: RoutePart): Mode {
  return parseMode(RAW_PART_MODES[part], `NEXT_PUBLIC_RFQ_MODE_${part.toUpperCase()}`) ?? defaultMode();
}

/** Base URL for a part, or a ConfigError when an `http` part has none. */
export function baseUrlOf(part: RoutePart): string {
  if (modeOf(part) === "mock") return "/api/mock";
  if (!API_BASE) {
    throw new ConfigError(
      `'${part}' 부분이 http 모드인데 NEXT_PUBLIC_RFQ_API_BASE가 비어 있습니다. 목업으로 대신하지 않습니다.`,
    );
  }
  return API_BASE.replace(/\/$/, "");
}

/** True when every part is `http`: mock routes and demo params must be off. */
export function allHttp(): boolean {
  try {
    return ROUTE_PARTS.every((p) => modeOf(p) === "http");
  } catch {
    return false;
  }
}
