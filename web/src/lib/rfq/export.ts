/**
 * Client-side exports (TASKS S013, RTM-02, ROUTE-04). Files are built from the
 * data the server already filtered for the account, so locked clauses stay
 * locked. The first line says how the data was computed (engine) and for which scope.
 */
import type { Engines, RtmRow, VisibleClause } from "./contract";
import { LOCKED_CLAUSE } from "./scope";

/** First-line note of an export: the engine that computed the data, and the scope it was made with. */
export function exportNote(engines: Engines | null, scope: string): string | null {
  if (!engines) return null;
  const how = engines.llm_connected ? `LLM 분석 (${engines.analysis})` : "규칙 기반 분석 (LLM 미연결)";
  return `${how} · 번역: ${engines.translation} · 원문 위치로 확인한 뒤 사용하세요. · 만든 범위: ${scope}`;
}

function csvCell(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

type Labels = { dept: (id: string) => string; file: (id: string) => string };

export function rtmCsv(rows: RtmRow[], labels: Labels, note: string | null): string {
  const header = [
    "clause_id",
    "file",
    "section_id",
    "title",
    "primary_department",
    "reference_departments",
    "priority",
    "red_flag",
    "page",
    "sentence",
    "anchor",
    "citation_status",
  ];
  const lines = rows.map((r) =>
    r.kind === "masked"
      ? [r.clause_id, labels.file(r.source_doc), r.section_id, LOCKED_CLAUSE, "", "", "", "", "", "", "", ""]
      : [
          r.clause_id,
          labels.file(r.source_doc),
          r.section_id,
          r.title,
          labels.dept(r.primary_department),
          r.reference_departments.map(labels.dept).join(" / "),
          r.priority,
          r.red_flag?.label ?? "",
          String(r.page_number),
          String(r.sentence_index),
          r.anchor ?? "",
          r.citation_status,
        ],
  );
  const body = [header, ...lines].map((l) => l.map(csvCell).join(",")).join("\n");
  // BOM so spreadsheet tools read Korean correctly.
  return `﻿${note ? `# ${note}\n` : ""}${body}\n`;
}

export function departmentBrief(
  deptName: string,
  clauses: VisibleClause[],
  maskedIds: string[],
  labels: Labels,
  note: string | null,
): string {
  const lines: string[] = [];
  if (note) lines.push(`> ${note}`, "");
  lines.push(`# ${deptName} 정리본`, "");
  for (const c of clauses) {
    const cites = c.summary.finding.citations
      .map((l) => `${labels.file(l.source_doc)} ${l.page_number}페이지 ${l.sentence_index}번째 문장`)
      .join(", ");
    const flags = [c.priority.toUpperCase(), c.red_flag ? `독소조항(${c.red_flag.label}): ${c.red_flag.reason}` : null]
      .filter(Boolean)
      .join(" · ");
    const summary = c.summary.finding.text || "요약 없음 (LLM 미연결). 원문 발췌를 확인하세요.";
    const roles = [`주관 ${labels.dept(c.primary_department)}`, ...c.reference_departments.map((d) => `참조 ${labels.dept(d)}`)];
    lines.push(`## ${c.section_id} ${c.title || "(표시할 제목 없음)"}`, "", `- ${flags}`, `- ${roles.join(", ")}`);
    lines.push(`- 요약: ${summary}`);
    lines.push(`- 근거: ${cites || "없음"}${c.summary.citation_status === "unverified" ? " (미검증)" : ""}`, "");
  }
  if (maskedIds.length) lines.push(`${LOCKED_CLAUSE}: ${maskedIds.join(", ")}`, "");
  return lines.join("\n");
}

export function download(fileName: string, content: string | Blob, type: string): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function exportName(base: string, ext: string): string {
  const date = new Date().toISOString().slice(0, 10);
  return `${base}_${date}.${ext}`;
}
