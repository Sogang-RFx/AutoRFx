/** Helpers of the account screen. The server decides; these only build requests and say what is missing. */
import { MAX_PASSWORD, MIN_PASSWORD } from "./auth-messages";
import type { AdminUser, AdminUserCreateRequest, AdminUserPatchRequest, AuditEntry, Department, Role } from "./contract";

export type AccountDraft = { display_name: string; role: Role; department: string };
export type NewAccountDraft = AccountDraft & { username: string; password: string };

export const ROLE_LABEL: Record<Role, string> = { admin: "관리자", member: "구성원" };
export const OUTCOME_LABEL: Record<AuditEntry["outcome"], string> = { ok: "완료", denied: "거부", failed: "실패" };

const USERNAME = /^[a-z0-9][a-z0-9_-]{2,31}$/;

/** An administrator sees everything and belongs to no department. */
const departmentOf = (d: AccountDraft): string | null => (d.role === "admin" ? null : d.department || null);

export const departmentScope = (d: Department): string => (d.receives_all ? "전체" : "자기 부서");
export const departmentCost = (d: Department): string => (d.cost_visible ? "열람함" : "열람하지 않음");

export function toDraft(user: AdminUser): AccountDraft {
  return { display_name: user.display_name, role: user.role, department: user.department ?? "" };
}

/** What changed, or null when nothing did. */
export function toPatch(user: AdminUser, draft: AccountDraft): AdminUserPatchRequest | null {
  const name = draft.display_name.trim();
  const department = departmentOf(draft);
  const patch: AdminUserPatchRequest = {
    ...(name !== user.display_name && { display_name: name }),
    ...(draft.role !== user.role && { role: draft.role }),
    ...(department !== user.department && { department }),
  };
  return Object.keys(patch).length > 0 ? patch : null;
}

export function draftProblem(draft: AccountDraft): string | null {
  if (!draft.display_name.trim()) return "이름을 넣으세요.";
  if (draft.role === "member" && !draft.department) return "구성원 계정은 부서를 골라야 합니다.";
  return null;
}

export function passwordProblem(password: string): string | null {
  if (password.length < MIN_PASSWORD) return `임시 비밀번호는 ${MIN_PASSWORD}자 이상으로 정하세요.`;
  if (password.length > MAX_PASSWORD) return `임시 비밀번호는 ${MAX_PASSWORD}자까지 쓸 수 있습니다.`;
  return null;
}

export function createProblem(draft: NewAccountDraft): string | null {
  if (!USERNAME.test(draft.username.trim()))
    return "아이디는 영문 소문자나 숫자로 시작해 3~32자로 넣으세요(소문자, 숫자, -, _).";
  return draftProblem(draft) ?? passwordProblem(draft.password);
}

export function toCreateRequest(draft: NewAccountDraft): AdminUserCreateRequest {
  return {
    username: draft.username.trim(),
    display_name: draft.display_name.trim(),
    password: draft.password,
    role: draft.role,
    department: departmentOf(draft),
  };
}

export const formatTime = (ms: number): string =>
  new Date(ms).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
