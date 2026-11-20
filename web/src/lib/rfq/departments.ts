/**
 * Department filter (SPEC US10). A rule department stands for itself; a company
 * department stands for the rule departments ("업무 영역") it receives.
 */
import type { Department, Team } from "./contract";

export type DeptFilter = { id: string; label: string; areas: string[]; receives_all?: boolean };

/** The company's own departments when it defined some, otherwise the rule departments. */
export function departmentFilters(teams: Team[], departments: Department[]): DeptFilter[] {
  const company = teams.filter((t): t is Team & { areas: string[] } => Array.isArray(t.areas));
  if (company.length === 0) {
    return departments.map((d) => ({ id: d.id, label: d.label, areas: [d.id], receives_all: d.receives_all }));
  }
  const receivesAll = new Set(departments.filter((d) => d.receives_all).map((d) => d.id));
  return company.map((t) => ({
    id: t.id,
    label: t.label,
    areas: t.areas,
    receives_all: t.areas.some((a) => receivesAll.has(a)),
  }));
}
