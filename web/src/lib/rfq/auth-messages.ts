/** Messages of the login and password screens. A wrong id and a wrong password read the same. */
import { ApiError, describeError } from "./provider";

export const MIN_PASSWORD = 8;
export const MAX_PASSWORD = 128;

const BAD_ORIGIN =
  "이 주소에서는 요청이 거절됩니다. 백엔드가 허용한 주소(예: http://localhost:3000)로 화면을 열어 주세요.";
const NETWORK = "백엔드에 연결하지 못했습니다. 서버가 켜져 있는지 확인하고 다시 시도하세요.";
const WRONG_PAIR = "아이디 또는 비밀번호가 맞지 않습니다.";

function common(e: ApiError): string | null {
  if (e.code === "network") return NETWORK;
  if (e.code === "bad_origin") return BAD_ORIGIN;
  return null;
}

export function loginError(e: unknown): string {
  if (!(e instanceof ApiError)) return describeError(e);
  const known = common(e);
  if (known) return known;
  if (e.code === "cookie_blocked")
    return "아이디와 비밀번호는 맞지만 브라우저가 로그인 쿠키를 저장하지 않았습니다. 화면과 백엔드 주소를 둘 다 localhost로 맞추세요(127.0.0.1은 다른 사이트입니다).";
  if (e.status === 429) return "로그인 시도가 너무 많아 잠시 막혔습니다. 5분쯤 뒤에 다시 시도하세요.";
  if (e.status === 401 || e.status === 422) return WRONG_PAIR;
  return describeError(e);
}

export function passwordError(e: unknown): string {
  if (!(e instanceof ApiError)) return describeError(e);
  const known = common(e);
  if (known) return known;
  if (e.status === 429) return "시도가 너무 많아 잠시 막혔습니다. 5분쯤 뒤에 다시 시도하세요.";
  if (e.status === 401) return "현재 비밀번호가 맞지 않습니다.";
  return describeError(e);
}

/** The first problem of a new password, or null. The server checks again. */
export function checkNewPassword(current: string, next: string, again: string): string | null {
  if (next.length < MIN_PASSWORD) return `새 비밀번호는 ${MIN_PASSWORD}자 이상으로 정하세요.`;
  if (next.length > MAX_PASSWORD) return `새 비밀번호는 ${MAX_PASSWORD}자까지 쓸 수 있습니다.`;
  if (next !== again) return "새 비밀번호와 확인이 다릅니다.";
  if (next === current) return "지금과 다른 비밀번호로 정하세요.";
  return null;
}
