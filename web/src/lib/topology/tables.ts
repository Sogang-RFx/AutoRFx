/** The same content as the map, as rows: what the list view and the no-WebGL fallback show. */
import type { TopoModel } from "./model";

export interface DepartmentRow {
  id: string;
  label: string;
  lead: number;
  ref: number;
  redFlags: number;
}

export interface FileRow {
  id: string;
  tag: string;
  name: string;
  visible: number;
  hidden: number;
  redFlags: number;
  issues: number | null;
}

export function departmentRows(model: TopoModel): DepartmentRow[] {
  return model.departments.map((d, i) => ({
    id: d.id,
    label: d.label,
    lead: d.lead,
    ref: d.ref,
    redFlags: model.clauses.filter((c) => c.lead === i && c.redFlag !== null).length,
  }));
}

export const fileRows = (model: TopoModel): FileRow[] =>
  model.files.map((f) => ({ id: f.id, tag: f.tag, name: f.name, visible: f.visible, hidden: f.hidden, redFlags: f.redFlags, issues: f.issues }));

/** The one line under the title: what is drawn and how much. */
export function mapSummary(model: TopoModel): string {
  const visible = model.clauses.length - model.hiddenClauses;
  const base = `파일 ${model.files.length}개의 조항 ${visible}개가 주관 부서 ${model.departments.filter((d) => d.lead > 0).length}곳으로 이어지는 모습입니다.`;
  return model.hiddenClauses > 0 ? `${base} 열람 권한이 없는 조항 ${model.hiddenClauses}개는 선 없이 표시됩니다.` : base;
}
