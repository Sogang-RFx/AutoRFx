"use client";
/** RTM table (TASKS S013, SPEC §14, US2): filters, sort, CSV and per-department brief. */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { IconDownload, IconLock, IconWarn } from "@/components/ui/icons";
import { Button, EmptyState, ErrorState, Panel, PriorityChip, RedFlagChip, Skeleton, cx } from "@/components/ui/primitives";
import type { Priority, RtmRow, VisibleClause } from "@/lib/rfq/contract";
import { departmentBrief, download, exportName, hasMock, rtmCsv } from "@/lib/rfq/export";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";
import { useResource } from "@/lib/rfq/use-resource";

const PRIORITY_ORDER: Record<Priority, number> = { must: 0, should: 1, optional: 2, info: 3 };
type SortKey = "section" | "priority";

export default function RtmPage() {
  const { id } = useParams<{ id: string }>();
  const rfqId = decodeURIComponent(id);
  const session = useSession();
  const ctx = { team: session.team, scenario: session.scenario };
  const [department, setDepartment] = useState<string>("");
  const [onlyRedFlags, setOnlyRedFlags] = useState(false);
  const [sort, setSort] = useState<SortKey>("section");
  const [exportError, setExportError] = useState<string | null>(null);

  const meta = useResource(session.ready ? "meta" : null, (s) => rfqApi.meta(s));
  const departments = meta.status === "ok" ? meta.data.data.departments : [];
  const deptLabel = (d: string) => departments.find((x) => x.id === d)?.label ?? d;
  const rtm = useResource(session.ready ? `rtm:${rfqId}:${ctx.team ?? "none"}:${ctx.scenario}` : null, (s) =>
    rfqApi.rtm(rfqId, ctx, s),
  );

  const rows = useMemo(() => {
    const all = rtm.status === "ok" ? rtm.data.data.rows : [];
    const filtered = all.filter((r) => {
      if (r.kind === "masked") return !onlyRedFlags;
      if (department && !r.departments.includes(department)) return false;
      return !onlyRedFlags || r.red_flag !== null;
    });
    if (sort === "section") return filtered;
    const rank = (r: RtmRow) => (r.kind === "clause" ? PRIORITY_ORDER[r.priority] : 9);
    return [...filtered].sort((a, b) => rank(a) - rank(b));
  }, [rtm, department, onlyRedFlags, sort]);

  const isMock = rtm.status === "ok" && hasMock(rtm.data.provenance);

  async function exportBrief() {
    setExportError(null);
    try {
      const res = await rfqApi.clauses(rfqId, ctx, department || null);
      const visible = res.data.clauses.filter((c): c is VisibleClause => c.kind === "clause");
      const masked = res.data.clauses.filter((c) => c.kind === "masked").map((c) => c.section_id);
      const name = department ? deptLabel(department) : "전체 부서";
      const mock = hasMock(res.provenance);
      download(
        exportName(`AutoRFx_정리본_${name}`, "md", mock),
        departmentBrief(name, visible, masked, mock),
        "text/markdown;charset=utf-8",
      );
    } catch (e) {
      setExportError(describeError(e));
    }
  }

  const selectClass = "min-h-8 rounded-control border border-line-control bg-surface px-1.5 text-label text-ink";

  return (
    <div className="flex flex-col gap-4 p-3 md:p-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1 basis-72">
          <h1 className="text-title font-semibold text-ink">요구사항 추적표 (RTM)</h1>
          <p className="text-body text-ink-muted">조항마다 담당 부서, 요건 강도, 독소조항, 원문 위치를 한 줄로 봅니다.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={rtm.status !== "ok"}
            data-testid="export-rtm"
            onClick={() =>
              download(exportName("AutoRFx_RTM", "csv", isMock), rtmCsv(rows, deptLabel, isMock), "text/csv;charset=utf-8")
            }
          >
            <IconDownload size={14} /> RTM 내려받기 (CSV)
          </Button>
          <Button disabled={rtm.status !== "ok"} onClick={exportBrief} data-testid="export-brief">
            <IconDownload size={14} /> {department ? `${deptLabel(department)} ` : ""}정리본 (Markdown)
          </Button>
        </div>
      </div>
      {exportError && <ErrorState message={exportError} />}

      <Panel
        title="추적표"
        labelId="rtm-title"
        provenance={rtm.status === "ok" ? rtm.data.provenance : undefined}
        actions={
          <div className="flex flex-wrap items-center gap-2 text-label">
            <label className="flex items-center gap-1 text-ink-muted">
              부서
              <select value={department} onChange={(e) => setDepartment(e.target.value)} className={selectClass}>
                <option value="">전체</option>
                {departments.map((d) => (
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
            <table className="w-full min-w-[760px] text-left text-dense" data-testid="rtm-table">
              <thead className="border-b border-line bg-canvas text-label text-ink-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">조항</th>
                  <th scope="col" className="px-3 py-2 font-semibold">제목</th>
                  <th scope="col" className="px-3 py-2 font-semibold">담당 부서</th>
                  <th scope="col" className="px-3 py-2 font-semibold">요건 강도</th>
                  <th scope="col" className="px-3 py-2 font-semibold">독소조항</th>
                  <th scope="col" className="px-3 py-2 font-semibold">원문 위치</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <Row key={r.section_id} row={r} rfqId={rfqId} deptLabel={deptLabel} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}

function Row({ row, rfqId, deptLabel }: { row: RtmRow; rfqId: string; deptLabel: (d: string) => string }) {
  if (row.kind === "masked") {
    return (
      <tr className="h-11 border-b border-line text-ink-muted" data-testid="rtm-masked">
        <td className="px-4 font-semibold tabular">{row.section_id}</td>
        <td colSpan={5} className="px-3">
          <span className="inline-flex items-center gap-1.5">
            <IconLock size={14} /> 이 팀에는 표시되지 않음
          </span>
        </td>
      </tr>
    );
  }
  const verified = row.citation_status === "verified";
  return (
    <tr className="border-b border-line align-top transition-soft hover:bg-canvas">
      <td className="px-4 py-2.5 font-semibold text-ink tabular">{row.section_id}</td>
      <td className="px-3 py-2.5 text-ink">{row.title}</td>
      <td className="px-3 py-2.5 text-ink-muted">{row.departments.map(deptLabel).join(" · ")}</td>
      <td className="px-3 py-2.5">
        <PriorityChip priority={row.priority} />
      </td>
      <td className="px-3 py-2.5">
        {row.red_flag ? <RedFlagChip type={row.red_flag} /> : <span className="text-ink-muted">–</span>}
      </td>
      <td className="px-3 py-2.5">
        <Link
          href={`/rfq/${encodeURIComponent(rfqId)}/clauses`}
          className={cx("inline-flex items-center gap-1 tabular hover:underline", verified ? "text-ok" : "text-warn")}
        >
          {!verified && <IconWarn size={12} />}
          {row.page_number}페이지 {row.sentence_index}번째 문장
          {!verified && " · 미검증"}
        </Link>
      </td>
    </tr>
  );
}
