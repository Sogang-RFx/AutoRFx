"use client";
/**
 * Cross-document consistency (contract v2 `ConsistencyResponse`): value
 * conflicts between files, missing attachments, broken references and notation
 * differences. Left: issues grouped by category. Right: the evidence side by
 * side on the paper surface, and the source viewer opened at the chosen file.
 * Issues whose evidence the account may not see in full are lock rows.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { SourceViewer } from "@/components/source-viewer/source-viewer";
import { IconArrowRight, IconLock } from "@/components/ui/icons";
import {
  CitationChip,
  EmptyState,
  ErrorState,
  Panel,
  Skeleton,
  cx,
  locusKey,
  sameLocus,
  useLocusLabel,
} from "@/components/ui/primitives";
import type { IssueCategory, IssueEvidence, IssueItem, Locus, VisibleIssue } from "@/lib/rfq/contract";
import { fileName, fileTag, usePackageFiles } from "@/lib/rfq/package-context";
import { rfqApi } from "@/lib/rfq/provider";
import { LOCKED_ISSUE, isCostVisible } from "@/lib/rfq/scope";
import { useViewer } from "@/lib/rfq/session";
import { useMeta } from "@/lib/rfq/use-meta";
import { useResource } from "@/lib/rfq/use-resource";

const CATEGORY_LABEL: Record<IssueCategory, string> = {
  conflict: "값 충돌",
  missing_attachment: "첨부 누락",
  broken_reference: "끊긴 참조",
  notation: "표기 불일치",
};
const CATEGORY_ORDER: IssueCategory[] = ["conflict", "missing_attachment", "broken_reference", "notation"];

const SEVERITY: Record<VisibleIssue["severity"], { label: string; bars: number }> = {
  high: { label: "높음", bars: 3 },
  medium: { label: "보통", bars: 2 },
  low: { label: "낮음", bars: 1 },
};

/** Three bars plus a word: severity is never shown by colour alone. */
function SeverityMark({ severity }: { severity: VisibleIssue["severity"] }) {
  const { label, bars } = SEVERITY[severity];
  return (
    <span className="inline-flex items-center gap-1 text-label text-ink-muted" aria-label={`심각도 ${label}`}>
      <span aria-hidden="true" className="inline-flex items-end gap-px">
        {[1, 2, 3].map((n) => (
          <span key={n} className={cx("w-1 rounded-chip", n <= bars ? "bg-ink" : "bg-line")} style={{ height: 4 + n * 3 }} />
        ))}
      </span>
      {label}
    </span>
  );
}

export default function ConsistencyPage() {
  const { id } = useParams<{ id: string }>();
  const viewer = useViewer();
  return <Consistency key={viewer.user_id} rfqId={decodeURIComponent(id)} />;
}

function Consistency({ rfqId }: { rfqId: string }) {
  const viewer = useViewer();
  const { departmentLabel } = useMeta();
  const res = useResource(`consistency:${rfqId}`, (s) => rfqApi.consistency(rfqId, s));
  const issues = useMemo(() => (res.status === "ok" ? res.data.data.issues : []), [res]);
  const [selected, setSelected] = useState<string | null>(null);
  const [target, setTarget] = useState<Locus | null>(null);

  const ordered = useMemo(() => {
    const visible = issues.filter((i): i is VisibleIssue => i.kind === "issue");
    const groups = CATEGORY_ORDER.map((cat) => ({ cat, items: visible.filter((i) => i.category === cat) })).filter(
      (g) => g.items.length > 0,
    );
    return { groups, masked: issues.filter((i) => i.kind === "masked") };
  }, [issues]);
  const flat: IssueItem[] = [...ordered.groups.flatMap((g) => g.items), ...ordered.masked];
  const current = flat.find((i) => i.id === selected) ?? null;

  function select(issueId: string) {
    setSelected(issueId);
    const issue = flat.find((i) => i.id === issueId);
    setTarget(issue?.kind === "issue" ? (issue.evidence[0]?.locus ?? null) : null);
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const ids = flat.map((i) => i.id);
    const idx = selected ? ids.indexOf(selected) : -1;
    const next = ids[e.key === "ArrowDown" ? Math.min(ids.length - 1, idx + 1) : Math.max(0, idx - 1)];
    if (next) {
      select(next);
      document.getElementById(`issue-${next}`)?.focus();
    }
  }

  const checked = res.status === "ok" ? res.data.data.checked : null;
  const nothingChecked = checked && checked.files + checked.entities + checked.references === 0;

  return (
    <div className="grid gap-3 p-3 md:p-4 lg:h-full lg:grid-cols-[380px_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)]">
      <Panel
        title="문서 간 정합성"
        labelId="issues-title"
        provenance={res.status === "ok" ? res.data.provenance : undefined}
        className="max-h-[70dvh] lg:max-h-none"
        bodyClassName="flex flex-col"
      >
        {checked && (
          <p className="border-b border-line px-4 py-2 text-label text-ink-muted tabular" data-testid="checked-summary">
            {nothingChecked
              ? "비교하지 않음: 비교할 파일이나 항목이 없습니다."
              : `파일 ${checked.files}개, 항목 ${checked.entities}개, 참조 ${checked.references}개를 비교했습니다 · 이슈 ${issues.length}건`}
          </p>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto" onKeyDown={onKeyDown}>
          {res.status === "loading" && <Skeleton lines={8} />}
          {res.status === "error" && <ErrorState message={res.error} />}
          {res.status === "ok" && issues.length === 0 && (
            <EmptyState title="찾은 어긋남이 없습니다">
              <p>위에 적힌 범위 안에서 파일 사이의 값과 참조가 서로 맞습니다.</p>
            </EmptyState>
          )}
          {ordered.groups.map((g) => (
            <section key={g.cat} aria-labelledby={`cat-${g.cat}`}>
              <h3 id={`cat-${g.cat}`} className="bg-canvas px-4 py-1.5 text-label font-semibold text-ink-muted tabular">
                {CATEGORY_LABEL[g.cat]} {g.items.length}
              </h3>
              <ul data-testid={`issues-${g.cat}`}>
                {g.items.map((issue) => (
                  <li key={issue.id} className="border-b border-line">
                    <button
                      id={`issue-${issue.id}`}
                      type="button"
                      data-testid="issue-row"
                      data-issue-id={issue.id}
                      aria-current={selected === issue.id}
                      onClick={() => select(issue.id)}
                      className={cx(
                        "relative flex w-full flex-col gap-1.5 px-4 py-3 text-left transition-soft",
                        selected === issue.id ? "bg-accent-tint" : "hover:bg-canvas",
                      )}
                    >
                      {selected === issue.id && <span aria-hidden="true" className="absolute inset-y-0 left-0 w-0.5 bg-accent" />}
                      <span className="text-dense font-medium text-ink">{issue.title}</span>
                      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <SeverityMark severity={issue.severity} />
                        {issue.affects_cost && <span className="text-label font-semibold text-ink">원가 영향</span>}
                        <FileTags evidence={issue.evidence} />
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {ordered.masked.length > 0 && (
            <section aria-labelledby="cat-masked">
              <h3 id="cat-masked" className="bg-canvas px-4 py-1.5 text-label font-semibold text-ink-muted tabular">
                자물쇠 이슈 {ordered.masked.length}
              </h3>
              <ul>
                {ordered.masked.map((m) => (
                  <li key={m.id} className="h-14 border-b border-line">
                    <button
                      id={`issue-${m.id}`}
                      type="button"
                      data-testid="issue-masked"
                      aria-current={selected === m.id}
                      onClick={() => select(m.id)}
                      className={cx(
                        "flex size-full items-center gap-2 px-4 text-left text-dense text-ink-muted transition-soft",
                        selected === m.id ? "bg-canvas" : "hover:bg-canvas",
                      )}
                    >
                      <IconLock size={14} className="shrink-0" /> {LOCKED_ISSUE}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </Panel>

      <div className="flex min-h-0 min-w-0 flex-col gap-3">
        <IssueDetail
          issue={current}
          rfqId={rfqId}
          restricted={!isCostVisible(viewer)}
          deptLabel={departmentLabel}
          target={target}
          onOpen={setTarget}
        />
        <SourceViewer
          rfqId={rfqId}
          target={target}
          className="min-h-[60dvh] lg:min-h-[420px] lg:flex-1"
        />
      </div>
    </div>
  );
}

function FileTags({ evidence }: { evidence: IssueEvidence[] }) {
  const files = usePackageFiles();
  const ids = [...new Set(evidence.map((e) => e.locus.source_doc))];
  if (files.length < 2) return null;
  return (
    <span className="text-label text-ink-muted" title={ids.map((i) => fileName(files, i)).join(" ↔ ")}>
      파일 {ids.map((i) => fileTag(files, i)).join(" ↔ ")}
    </span>
  );
}

/** The sentence with the stated value marked, so the difference is visible at a glance. */
function Highlighted({ text, value }: { text: string; value: string | null }): ReactNode {
  const at = value ? text.indexOf(value) : -1;
  if (!value || at < 0) return text;
  return (
    <>
      {text.slice(0, at)}
      <mark className="rounded-chip bg-mark px-0.5 text-ink">{value}</mark>
      {text.slice(at + value.length)}
    </>
  );
}

type DetailProps = {
  issue: IssueItem | null;
  rfqId: string;
  restricted: boolean;
  deptLabel: (d: string) => string;
  target: Locus | null;
  onOpen: (l: Locus) => void;
};

function IssueDetail({ issue, rfqId, restricted, deptLabel, target, onOpen }: DetailProps) {
  const files = usePackageFiles();
  const locusLabel = useLocusLabel();
  if (!issue) {
    return (
      <Panel title="이슈 상세" labelId="issue-detail-title">
        <EmptyState title="이슈를 고르세요">
          <p>목록에서 이슈를 고르면 파일별 근거를 나란히 보고, 원문에서 바로 열 수 있습니다.</p>
        </EmptyState>
      </Panel>
    );
  }
  if (issue.kind === "masked") {
    return (
      <Panel title="자물쇠 이슈" labelId="issue-detail-title">
        <div className="flex items-start gap-2 p-4 text-dense text-ink-muted" data-testid="issue-masked-detail">
          <IconLock size={16} className="mt-0.5 shrink-0" />
          <p>{LOCKED_ISSUE}. 이 계정의 범위로는 내용과 근거를 볼 수 없습니다.</p>
        </div>
      </Panel>
    );
  }
  const verified = issue.citation_status === "verified";
  const single = issue.evidence.length < 2;
  const emptySecond =
    issue.category === "missing_attachment"
      ? `참조된 '${issue.evidence[0]?.value ?? "첨부"}'가 패키지에 없습니다.`
      : issue.category === "broken_reference"
        ? `참조된 '${issue.evidence[0]?.value ?? "위치"}'를 문서에서 찾지 못했습니다.`
        : null;

  return (
    <Panel title={issue.title} labelId="issue-detail-title" bodyClassName="overflow-y-auto">
      <div className="flex flex-col gap-4 p-4" data-testid="issue-detail">
        <div className={cx("grid gap-3", (issue.evidence.length > 1 || emptySecond) && "xl:grid-cols-2")}>
          {issue.evidence.map((e, i) => (
            <article
              key={locusKey(e.locus, i)}
              className="flex flex-col gap-2 rounded-control border border-line bg-surface-source p-3"
              data-testid="evidence-card"
            >
              <header className="flex flex-wrap items-center gap-2 text-label text-ink-muted">
                <span className="min-w-0 truncate font-semibold text-ink" title={fileName(files, e.locus.source_doc)}>
                  {files.length > 1 && `${fileTag(files, e.locus.source_doc)} · `}
                  {fileName(files, e.locus.source_doc)}
                </span>
                <CitationChip locus={e.locus} verified={verified} onOpen={onOpen} isActive={sameLocus(target, e.locus)} />
              </header>
              <p className="whitespace-pre-line font-serif text-source text-ink">
                <Highlighted text={e.text} value={e.value} />
              </p>
              {(e.value || e.normalized) && (
                <p className="text-label text-ink-muted tabular">
                  적힌 값 <strong className="font-semibold text-ink">{e.value ?? "–"}</strong>
                  {e.normalized && e.normalized !== e.value && (
                    <>
                      {" "}
                      → 비교한 값 <strong className="font-semibold text-ink">{e.normalized}</strong>
                    </>
                  )}
                </p>
              )}
              <span className="sr-only">{locusLabel(e.locus)}</span>
            </article>
          ))}
          {single && emptySecond && (
            <div className="flex items-center rounded-control border border-dashed border-line-control p-3 text-dense text-ink-muted">
              {emptySecond}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-1.5 text-dense">
          <p className="text-ink">{issue.detail}</p>
          <p className="flex flex-wrap gap-x-3 text-label text-ink-muted">
            <span>{CATEGORY_LABEL[issue.category]}</span>
            <SeverityMark severity={issue.severity} />
            {issue.entity && <span>항목: {issue.entity}</span>}
            {issue.departments.length > 0 && <span>관련 부서: {issue.departments.map(deptLabel).join(", ")}</span>}
            <span>{verified ? "인용 검증됨" : "인용 미검증"}</span>
          </p>
          {issue.affects_cost && (
            <p className="flex flex-wrap items-center gap-2 text-label text-ink">
              <strong className="font-semibold">원가 영향:</strong> 이 충돌이 풀릴 때까지 견적 판정은 보류됩니다.
              {!restricted && (
                <Link
                  href={`/rfq/${encodeURIComponent(rfqId)}/precost`}
                  className="inline-flex items-center gap-1 text-accent hover:underline"
                >
                  사전원가에서 값 고르기 <IconArrowRight size={12} />
                </Link>
              )}
            </p>
          )}
        </div>
      </div>
    </Panel>
  );
}
