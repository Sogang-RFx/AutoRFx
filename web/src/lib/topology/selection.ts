/** What is chosen on the map, and how that changes colours and which edges are redrawn in the accent. */
import type { TopoModel } from "./model";

export type Selection = { kind: "clause"; id: string } | { kind: "department"; id: string } | null;

/** The colour of a box: ink by default, danger for a red flag, accent for the choice, a pale line colour for a hidden one. */
export type ClauseTone = "ink" | "danger" | "accent" | "hidden";

export type Resolved = { kind: "clause"; index: number } | { kind: "department"; index: number } | null;

/** Index of the chosen item; null when nothing is chosen or the id is gone (another account, a reload). */
export function resolveSelection(model: TopoModel, selection: Selection): Resolved {
  if (!selection) return null;
  if (selection.kind === "clause") {
    const index = model.clauses.findIndex((c) => c.id === selection.id && c.isVisible);
    return index < 0 ? null : { kind: "clause", index };
  }
  const index = model.departments.findIndex((d) => d.id === selection.id);
  return index < 0 ? null : { kind: "department", index };
}

export function clauseTones(model: TopoModel, chosen: Resolved): ClauseTone[] {
  return model.clauses.map((c, i) => {
    if (!c.isVisible) return "hidden";
    if (chosen?.kind === "clause" && chosen.index === i) return "accent";
    // A flag stays visible when a whole department is chosen; its edge shows the membership.
    if (c.redFlag) return "danger";
    if (chosen?.kind === "department" && c.lead === chosen.index) return "accent";
    return "ink";
  });
}

/** Edge ids to redraw in the accent colour. */
export function accentEdges(model: TopoModel, chosen: Resolved): number[] {
  if (!chosen) return [];
  const ids: number[] = [];
  model.edges.forEach((e, i) => {
    if (chosen.kind === "clause" ? e.clause === chosen.index : e.department === chosen.index) ids.push(i);
  });
  return ids;
}

/** Copies the chosen edges' endpoints into one array for a line-segments object. */
export function pickSegments(edges: Float32Array, ids: readonly number[]): Float32Array {
  const out = new Float32Array(ids.length * 4);
  ids.forEach((id, k) => out.set(edges.subarray(id * 4, id * 4 + 4), k * 4));
  return out;
}

/** Department sheets: ink; the choice (or the lead of a chosen clause) accent; the others fade when something is chosen. */
export type SheetTone = "ink" | "accent" | "muted";

export function departmentTones(model: TopoModel, chosen: Resolved): SheetTone[] {
  return model.departments.map((_, i) => {
    if (!chosen) return "ink";
    if (chosen.kind === "department") return chosen.index === i ? "accent" : "muted";
    const clause = model.clauses[chosen.index];
    if (clause.lead === i) return "accent";
    return clause.refs.includes(i) ? "ink" : "muted";
  });
}
