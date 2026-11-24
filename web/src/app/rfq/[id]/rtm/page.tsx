"use client";
/**
 * RTM table (TASKS S013, SPEC §14, US2): lead and reference departments, file,
 * filters, sort, exports (server Excel, CSV, per-department brief) for the account's scope.
 * Each row links to its clause in the workspace.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { IconDownload, IconLock, IconWarn } from "@/components/ui/icons";
import { Button, EmptyState, ErrorState, FormMessage, Panel, PriorityChip, RedFlagChip, Skeleton, cx, formatLocus } from "@/components/ui/primitives";
import type { Priority, RtmRow, VisibleClause } from "@/lib/rfq/contract";
import { departmentBrief, download, exportName, exportNote, rtmCsv } from "@/lib/rfq/export";
import { fileName, fileTag, usePackageFiles } from "@/lib/rfq/package-context";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { LOCKED_CLAUSE, departmentOptions, inDepartment, scopeLine, scopeName } from "@/lib/rfq/scope";
import { useViewer } from "@/lib/rfq/session";
import { useMeta } from "@/lib/rfq/use-meta";
import { useResource } from "@/lib/rfq/use-resource";

const PRIORITY_ORDER: Record<Priority, number> = { must: 0, should: 1, optional: 2, info: 3 };
type SortKey = "section" | "priority";

export default function RtmPage() {
  const { id } = useParams<{ id: string }>();
  const rfqId = decodeURIComponent(id);
  const viewer = useViewer();
  const { departments, departmentLabel: deptLabel, engines } = useMeta();
  const files = usePackageFiles();
  const [department, setDepartment] = useState<string>("");
  const [onlyRedFlags, setOnlyRedFlags] = useState(false);
  const [sort, setSort] = useState<SortKey>("section");
  const [exportError, setExportError] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  const labels = { dept: deptLabel, file: (f: string) => fileName(files, f) };
  const rtm = useResource(`rtm:${rfqId}`, (s) => rfqApi.rtm(rfqId, s));
  const all = useMemo(() => (rtm.status === "ok" ? rtm.data.data.rows : []), [rtm]);
  const dept = departments.find((d) => d.id === department) ?? null;
  const note = exportNote(engines, scopeName(viewer));

  const rows = useMemo(() => {
    const filtered = all.filter((r) => inDepartment(r, dept) && (!onlyRedFlags || (r.kind === "clause" && r.red_flag !== null)));
    if (sort === "section") return filtered;
    const rank = (r: RtmRow) => (r.kind === "clause" ? PRIORITY_ORDER[r.priority] : 9);
    return [...filtered].sort((a, b) => rank(a) - rank(b));
  }, [all, dept, onlyRedFlags, sort]);

  /** Runs one export; a refusal (401, 403) is told here instead of opening the server's answer. */
  async function runExport(make: () => Promise<void>) {
    setExportError(null);
    setIsExporting(true);
    try {
      await make();
    } catch (e) {
      setExportError(describeError(e));
    } finally {
      setIsExporting(false);
    }
  }

  async function exportXlsx() {
    const file = await rfqApi.rtmXlsx(rfqId);
    download(exportName("AutoRFx_RTM", "xlsx"), file, file.type);
  }

  async function exportBrief() {
    const res = await rfqApi.clauses(rfqId);
    const items = res.data.clauses.filter((c) => inDepartment(c, dept));
    const visible = items.filter((c): c is VisibleClause => c.kind === "clause");
    const masked = items.filter((c) => c.kind === "masked").map((c) => c.clause_id);
    const name = dept ? dept.label : "전체 부서";
    download(
      exportName(`AutoRFx_정리본_${name}`, "md"),
      departmentBrief(name, visible, masked, labels, note),
      "text/markdown;charset=utf-8",
    );
  }

  const selectClass = "min-h-8 rounded-control border border-line-control bg-surface px-1.5 text-label text-ink";

  return (
    <div className="flex flex-col gap-4 p-3 md:p-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1 basis-72">
          <h1 className="text-title font-semibold text-ink">요구사항 추적표 (RTM)</h1>
          <p className="text-body text-ink-muted">
            조항마다 파일, 주관·참조 부서, 요건 강도, 독소조항, 원문 위치를 한 줄로 봅니다.
          </p>
          {rtm.status === "ok" && (
            <p className="text-label font-semibold text-ink tabular" data-testid="scope-line">
              {scopeLine(viewer, all)}
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={rtm.status !== "ok" || isExporting}
            onClick={() => runExport(exportXlsx)}
            data-testid="export-rtm-xlsx"
          >
            <IconDownload size={14} /> RTM 내려받기 (Excel)
          </Button>
          <Button
            disabled={rtm.status !== "ok"}
            data-testid="export-rtm"
            onClick={() =>
              download(
                exportName("AutoRFx_RTM", "csv"),
                rtmCsv(rows, labels, note),
                "text/csv;charset=utf-8",
              )
            }
          >
            <IconDownload size={14} /> CSV
          </Button>
          <Button
            disabled={rtm.status !== "ok" || isExporting}
            onClick={() => runExport(exportBrief)}
            data-testid="export-brief"
          >
            <IconDownload size={14} /> {dept ? `${dept.label} ` : ""}정리본 (Markdown)
          </Button>
        </div>
      </div>
      {exportError && <FormMessage tone="error">내려받지 못했습니다. {exportError}</FormMessage>}

      <Panel
        title="추적표"
        labelId="rtm-title"
        provenance={rtm.status === "ok" ? rtm.data.provenance : undefined}
        actions={
          <div className="flex flex-wrap items-center gap-2 text-label">
            <label className="flex items-center gap-1 text-ink-muted">
              보기: 부서
              <select value={department} onChange={(e) => setDepartment(e.target.value)} className={selectClass}>
                <option value="">전체</option>
                {departmentOptions(all, departments).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1 text-ink-muted">
              정렬
              <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} className={selectClass}>
                <option value="section">조항 순</option>
                <option value="priority">요건 강도 순</option>
              </select>
            </label>
            <label className="flex items-center gap-1 text-ink-muted">
              <input type="checkbox" checked={onlyRedFlags} onChange={(e) => setOnlyRedFlags(e.target.checked)} />
              독소조항만
            </label>
          </div>
        }
      >
        {rtm.status === "loading" && <Skeleton lines={10} />}
        {rtm.status === "error" && <ErrorState message={rtm.error} />}
        {rtm.status === "ok" && rows.length === 0 && <EmptyState title="조건에 맞는 행이 없습니다" />}
        {rtm.status === "ok" && rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[880px] text-left text-dense" data-testid="rtm-table">
              <thead className="border-b border-line bg-canvas text-label text-ink-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">조항</th>
                  <th scope="col" className="px-3 py-2 font-semibold">제목</th>
                  <th scope="col" className="px-3 py-2 font-semibold">주관</th>
                  <th scope="col" className="px-3 py-2 font-semibold">참조</th>
                  <th scope="col" className="px-3 py-2 font-semibold">요건 강도</th>
                  <th scope="col" className="px-3 py-2 font-semibold">독소조항</th>
                  <th scope="col" className="px-3 py-2 font-semibold">원문 위치</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <Row key={r.clause_id} row={r} rfqId={rfqId} deptLabel={deptLabel} tag={files.length > 1 ? fileTag(files, r.source_doc) : null} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}

type RowProps = { row: RtmRow; rfqId: string; deptLabel: (d: string) => string; tag: string | null };

function Row({ row, rfqId, deptLabel, tag }: RowProps) {
  const files = usePackageFiles();
  const number = (
    <td className="px-4 py-2.5 font-semibold text-ink tabular" title={fileName(files, row.source_doc)}>
      {tag && <span className="mr-1 text-label font-medium text-ink-muted">{tag}</span>}
      {row.section_id}
    </td>
  );
  if (row.kind === "masked") {
    return (
      <tr className="h-11 border-b border-line text-ink-muted" data-testid="rtm-masked">
        {number}
        <td colSpan={6} className="px-3">
          <span className="inline-flex items-center gap-1.5">
            <IconLock size={14} className="shrink-0" /> {LOCKED_CLAUSE}
          </span>
        </td>
      </tr>
    );
  }
  const verified = row.citation_status === "verified";
  const href = `/rfq/${encodeURIComponent(rfqId)}/clauses?clause=${encodeURIComponent(row.clause_id)}`;
  return (
    <tr className="border-b border-line align-top transition-soft hover:bg-canvas" data-testid="rtm-row">
      {number}
      <td className="px-3 py-2.5 text-ink">{row.title || <span className="text-ink-muted">(표시할 제목 없음)</span>}</td>
      <td className="px-3 py-2.5 font-semibold text-ink">{deptLabel(row.primary_department)}</td>
      <td className="px-3 py-2.5 text-ink-muted">{row.reference_departments.map(deptLabel).join(", ") || "–"}</td>
      <td className="px-3 py-2.5">
        <PriorityChip priority={row.priority} />
      </td>
      <td className="px-3 py-2.5">
        {row.red_flag ? <RedFlagChip label={row.red_flag.label} /> : <span className="text-ink-muted">–</span>}
      </td>
      <td className="px-3 py-2.5">
        <Link href={href} className={cx("inline-flex items-center gap-1 tabular hover:underline", verified ? "text-ok" : "text-warn")}>
          {!verified && <IconWarn size={12} />}
          {formatLocus(row, tag ?? undefined)}
          {!verified && " · 미검증"}
        </Link>
      </td>
    </tr>
  );
}
