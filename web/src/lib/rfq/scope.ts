/**
 * What the signed-in account sees, in words, and the department view filter. Nothing here decides
 * what may be seen: the server already left it out (docs/auth-rbac.md §1). The reason why a clause
 * is locked is not told, so no text here names one.
 */
import type { Department, MaskedClause, Viewer } from "./contract";

export const LOCKED_CLAUSE = "열람 권한이 없는 조항입니다";
export const LOCKED_ISSUE = "열람 권한이 없는 이슈입니다";

type VisibleItem = { kind: "clause"; primary_department: string; reference_departments: string[] };
type Item = VisibleItem | MaskedClause;

export const isCostVisible = (v: Viewer): boolean => v.group === "cost_visible";

/** Nothing is grey in this account's source PDF. */
export const seesWholeSource = (v: Viewer): boolean => v.full_scope && isCostVisible(v);

export function scopeName(v: Viewer): string {
  return v.full_scope ? "전체 범위" : `${v.department_label ?? v.department ?? "알 수 없는"} 부서 범위`;
}

/** "품질 부서 범위 · 조항 12 / 자물쇠 48" */
export function scopeLine(v: Viewer, items: readonly Item[]): string {
  const locked = items.filter((i) => i.kind === "masked").length;
  const shown = `${scopeName(v)} · 조항 ${items.length - locked}`;
  return locked > 0 || !v.full_scope ? `${shown} / 자물쇠 ${locked}` : shown;
}

/** Role of a department on a clause the account may see. */
export function departmentRole(item: VisibleItem, department: string): "primary" | "reference" | null {
  if (item.primary_department === department) return "primary";
  return item.reference_departments.includes(department) ? "reference" : null;
}

/** View filter. Lock rows stay under every department: the filter must not tell whose they are. */
export function inDepartment(item: Item, department: Department | null): boolean {
  if (!department || item.kind === "masked" || department.receives_all) return true;
  return departmentRole(item, department.id) !== null;
}

/** Departments of the clauses the account may see, in the order of the rules. */
export function departmentOptions(items: readonly Item[], departments: Department[]): Department[] {
  const present = new Set(
    items.flatMap((i) => (i.kind === "clause" ? [i.primary_department, ...i.reference_departments] : [])),
  );
  return departments.filter((d) => present.has(d.id));
}
