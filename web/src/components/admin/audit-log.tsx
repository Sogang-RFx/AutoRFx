"use client";
/** The last entries of the audit log: who did what, and whether it was allowed. */
import { OUTCOME_LABEL, formatTime } from "@/lib/rfq/admin";
import { rfqApi } from "@/lib/rfq/provider";
import { useResource } from "@/lib/rfq/use-resource";
import { Button, EmptyState, ErrorState, Panel, Skeleton, cx } from "../ui/primitives";

export function AuditLog({ version, onReload }: { version: number; onReload: () => void }) {
  const audit = useResource("admin:audit", (s) => rfqApi.audit(s), version);
  const entries = audit.status === "ok" ? audit.data.data.entries : [];
  return (
    <Panel
      title={audit.status === "ok" ? `감사 기록 최근 ${entries.length}건` : "감사 기록"}
      labelId="audit-title"
      actions={
        <Button variant="ghost" onClick={onReload}>
          새로고침
        </Button>
      }
    >
      {audit.status === "loading" && <Skeleton lines={5} />}
      {audit.status === "error" && <ErrorState message={audit.error} onRetry={onReload} />}
      {audit.status === "ok" && entries.length === 0 && <EmptyState title="아직 기록이 없습니다" />}
      {entries.length > 0 && (
        <div className="max-h-[60dvh] overflow-auto" tabIndex={0} role="region" aria-label="감사 기록 (방향키로 스크롤)">
          <table className="w-full min-w-[640px] text-left text-dense" data-testid="audit-table">
            <thead className="border-b border-line bg-canvas text-label text-ink-muted">
              <tr>
                <th scope="col" className="px-4 py-2 font-semibold">시각</th>
                <th scope="col" className="px-3 py-2 font-semibold">계정</th>
                <th scope="col" className="px-3 py-2 font-semibold">한 일</th>
                <th scope="col" className="px-3 py-2 font-semibold">대상</th>
                <th scope="col" className="px-3 py-2 font-semibold">결과</th>
                <th scope="col" className="px-3 py-2 font-semibold">내용</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e, i) => (
                <tr key={`${e.at}-${i}`} className="border-b border-line align-top last:border-b-0">
                  <td className="whitespace-nowrap px-4 py-2 text-ink-muted tabular">{formatTime(e.at)}</td>
                  <td className="px-3 py-2 text-ink">{e.actor ?? "로그인 전"}</td>
                  <td className="px-3 py-2 text-ink">{e.event}</td>
                  <td className="px-3 py-2 text-ink">{e.target ?? "–"}</td>
                  <td className={cx("px-3 py-2", e.outcome === "ok" ? "text-ink-muted" : "font-semibold text-danger")}>
                    {OUTCOME_LABEL[e.outcome]}
                  </td>
                  <td className="px-3 py-2 text-ink-muted">{e.detail || "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
