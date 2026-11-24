/**
 * Backend address (SPEC §13.2). The web app talks to the real backend only;
 * there is no mock mode and no fallback data (INV-10). A missing base URL is a
 * configuration error, shown where the request was made.
 *
 * NEXT_PUBLIC_* values are inlined at build time only when referenced
 * literally, so the variable is spelled out below.
 */

/** Endpoint groups, used to say which part of the screen a failure belongs to. */
export type RoutePart =
  | "auth"
  | "admin"
  | "document"
  | "analysis"
  | "source"
  | "consistency"
  | "cost_inputs"
  | "precost"
  | "chat"
  | "evaluation";

const API_BASE = process.env.NEXT_PUBLIC_RFQ_API_BASE ?? "";

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

/** Base URL of the backend, or a ConfigError when it is not set. */
export function baseUrlOf(part: RoutePart): string {
  if (!API_BASE) {
    throw new ConfigError(
      `NEXT_PUBLIC_RFQ_API_BASE가 비어 있어 '${part}' 부분을 백엔드에서 받을 수 없습니다. 다른 데이터로 대신하지 않습니다.`,
    );
  }
  return API_BASE.replace(/\/$/, "");
}
