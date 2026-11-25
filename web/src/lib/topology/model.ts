/**
 * The flow map as data: files, clauses grouped by file, lead departments and the edges between clauses
 * and their lead department. Pure: no React, no three.js.
 *
 * A clause the account may not see (`masked`) is kept exactly as far as the response describes it:
 * its file and its identity. It has no department and no edge, and nothing here is derived from it
 * except the count (INV-8). A department is never guessed.
 */
import type {
  ClauseItem,
  ConsistencyResponse,
  PackageFile,
  Priority,
  RedFlagRef,
} from "@/lib/rfq/contract";
import { fileTag } from "@/lib/rfq/package-context";

export interface TopoFile {
  id: string;
  name: string;
  /** A, B, C … in package order, as on the other screens. */
  tag: string;
  /** Clauses the account may see / may not see, in this file. */
  visible: number;
  hidden: number;
  redFlags: number;
  /** Issues whose evidence is in this file; null when the consistency response is not here. */
  issues: number | null;
}

export interface TopoClause {
  id: string;
  /** Index into `files`. */
  file: number;
  isVisible: boolean;
  /** Section number and title; empty for a clause the account may not see. */
  number: string;
  title: string;
  /** Index into `departments`; null for a clause the account may not see. */
  lead: number | null;
  refs: number[];
  priority: Priority | null;
  redFlag: RedFlagRef | null;
}

export interface TopoDepartment {
  id: string;
  label: string;
  /** Clauses where it is the lead / a reference. */
  lead: number;
  ref: number;
}

/** One line from a visible clause to its lead department. */
export interface TopoEdge {
  clause: number;
  department: number;
}

export interface TopoModel {
  files: TopoFile[];
  clauses: TopoClause[];
  departments: TopoDepartment[];
  edges: TopoEdge[];
  hiddenClauses: number;
  redFlags: number;
  /** Visible / hidden issues; null when the consistency response is not here. */
  issues: { visible: number; hidden: number } | null;
}

export interface ModelInput {
  /** The files of the package, in package order. */
  files: PackageFile[];
  clauses: ClauseItem[];
  consistency: ConsistencyResponse["data"] | null;
  /** Departments in the order of the rules, with their labels. */
  departments: { id: string; label: string }[];
}

/** Package order first; a file only the clauses name comes after, in first-seen order. */
function fileIds(input: ModelInput): string[] {
  const ids = input.files.map((f) => f.id);
  const known = new Set(ids);
  for (const c of input.clauses) {
    if (known.has(c.source_doc)) continue;
    known.add(c.source_doc);
    ids.push(c.source_doc);
  }
  return ids;
}

/** Rule order first; an id only the clauses name comes after, sorted so the order is stable. */
function departmentIds(input: ModelInput): string[] {
  const used = new Set<string>();
  for (const c of input.clauses) {
    if (c.kind !== "clause") continue;
    used.add(c.primary_department);
    c.reference_departments.forEach((d) => used.add(d));
  }
  const ruled = input.departments.map((d) => d.id).filter((id) => used.has(id));
  const known = new Set(ruled);
  const extra = [...used].filter((id) => !known.has(id)).sort();
  return [...ruled, ...extra];
}

function issueCounts(
  consistency: ConsistencyResponse["data"] | null,
  ids: string[],
): { perFile: Map<string, number>; summary: TopoModel["issues"] } {
  const perFile = new Map<string, number>(ids.map((id) => [id, 0]));
  if (!consistency) return { perFile, summary: null };
  let visible = 0;
  let hidden = 0;
  for (const issue of consistency.issues) {
    if (issue.kind === "masked") {
      hidden += 1;
      continue;
    }
    visible += 1;
    // An issue counts once for each file its evidence is in.
    for (const file of new Set(issue.evidence.map((e) => e.locus.source_doc))) {
      if (perFile.has(file)) perFile.set(file, (perFile.get(file) ?? 0) + 1);
    }
  }
  return { perFile, summary: { visible, hidden } };
}

function toClause(item: ClauseItem, file: number, deptIndex: Map<string, number>): TopoClause {
  if (item.kind === "masked") {
    return { id: item.clause_id, file, isVisible: false, number: "", title: "", lead: null, refs: [], priority: null, redFlag: null };
  }
  return {
    id: item.clause_id,
    file,
    isVisible: true,
    number: item.section_id,
    title: item.title,
    lead: deptIndex.get(item.primary_department) ?? null,
    refs: item.reference_departments.flatMap((d) => deptIndex.get(d) ?? []),
    priority: item.priority,
    redFlag: item.red_flag ? { type: item.red_flag.type, label: item.red_flag.label } : null,
  };
}

export function buildModel(input: ModelInput): TopoModel {
  const ids = fileIds(input);
  const fileIndex = new Map(ids.map((id, i) => [id, i]));
  const deptIds = departmentIds(input);
  const deptIndex = new Map(deptIds.map((id, i) => [id, i]));
  const labels = new Map(input.departments.map((d) => [d.id, d.label]));
  const issues = issueCounts(input.consistency, ids);

  const clauses = input.clauses.map((c) => toClause(c, fileIndex.get(c.source_doc) ?? 0, deptIndex));
  const departments: TopoDepartment[] = deptIds.map((id) => ({ id, label: labels.get(id) ?? id, lead: 0, ref: 0 }));
  const edges: TopoEdge[] = [];
  clauses.forEach((c, i) => {
    if (c.lead !== null) {
      departments[c.lead].lead += 1;
      edges.push({ clause: i, department: c.lead });
    }
    c.refs.forEach((r) => (departments[r].ref += 1));
  });

  const files: TopoFile[] = ids.map((id, i) => {
    const mine = clauses.filter((c) => c.file === i);
    return {
      id,
      name: input.files.find((f) => f.id === id)?.name ?? id,
      tag: fileTag(input.files, id),
      visible: mine.filter((c) => c.isVisible).length,
      hidden: mine.filter((c) => !c.isVisible).length,
      redFlags: mine.filter((c) => c.redFlag !== null).length,
      issues: input.consistency ? (issues.perFile.get(id) ?? 0) : null,
    };
  });

  return {
    files,
    clauses,
    departments,
    edges,
    hiddenClauses: clauses.filter((c) => !c.isVisible).length,
    redFlags: clauses.filter((c) => c.redFlag !== null).length,
    issues: issues.summary,
  };
}
