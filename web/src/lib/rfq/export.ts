/**
 * Client-side exports (TASKS S013, RTM-02, ROUTE-04). Files are built from the
 * data the server already filtered for the team, so masked clauses stay
 * masked. Any file that contains mock data says so in its name and first line
 * (SPEC §13.4).
 */
import type { ProvenanceMap, RtmRow, VisibleClause } from "./contract";

export function hasMock(p: ProvenanceMap | undefined): boolean {
  return Object.values(p ?? {}).includes("mock");
}

const MOCK_NOTE = "목업 데이터: 합성 샘플 RFQ의 시연용 결과이며 실제 분석 결과가 아닙니다.";

function csvCell(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

export function rtmCsv(rows: RtmRow[], deptLabel: (id: string) => string, isMock: boolean): string {
  const header = ["section_id", "title", "departments", "priority", "red_flag", "page", "sentence", "citation_status"];
  const lines = rows.map((r) =>
    r.kind === "masked"
      ? [r.section_id, "(이 팀에는 표시되지 않음)", "", "", "", "", "", ""]
      : [
          r.section_id,
          r.title,
          r.departments.map(deptLabel).join(" / "),
          r.priority,
          r.red_flag ?? "",
          String(r.page_number),
          String(r.sentence_index),
          r.citation_status,
        ],
  );
  const body = [header, ...lines].map((l) => l.map(csvCell).join(",")).join("\n");
  // BOM so spreadsheet tools read Korean correctly.
  return `﻿${isMock ? `# ${MOCK_NOTE}\n` : ""}${body}\n`;
}

export function departmentBrief(deptName: string, clauses: VisibleClause[], maskedIds: string[], isMock: boolean): string {
  const lines: string[] = [];
  if (isMock) lines.push(`> ${MOCK_NOTE}`, "");
  lines.push(`# ${deptName} 정리본`, "");
  for (const c of clauses) {
    const cites = c.summary.finding.citations.map((l) => `${l.page_number}페이지 ${l.sentence_index}번째 문장`).join(", ");
    const flags = [c.priority.toUpperCase(), c.red_flag ? `독소조항: ${c.red_flag.reason}` : null].filter(Boolean);
    lines.push(`## ${c.section_id} ${c.title}`, "", `- ${flags.join(" · ")}`, `- 요약: ${c.summary.finding.text}`);
    lines.push(`- 근거: ${cites || "없음"}${c.summary.citation_status === "unverified" ? " (미검증)" : ""}`, "");
  }
  if (maskedIds.length) lines.push(`가려진 조항: ${maskedIds.join(", ")} (이 팀에는 표시되지 않음)`, "");
  return lines.join("\n");
}

export function download(fileName: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function exportName(base: string, ext: string, isMock: boolean): string {
  const date = new Date().toISOString().slice(0, 10);
  return `${isMock ? "MOCK_" : ""}${base}_${date}.${ext}`;
}
