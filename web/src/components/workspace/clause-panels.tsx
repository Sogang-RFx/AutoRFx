/**
 * Client-only (imported from the client workspace page).
 * Clause list and clause detail (TASKS S011, S014, SPEC §14). Interpretation
 * sits on white sans surfaces; original text on the paper-coloured serif
 * surface. Locked clauses render as fixed-height rows with number + lock only.
 * Clause ids come from real documents and may contain spaces, colons or "§",
 * so they are never used raw as DOM ids or selectors (see `clauseDomId`).
 */
import { useState } from "react";
import type { KeyboardEvent } from "react";
import type { ClauseItem, Department, Locus, ProvenanceMap, VisibleClause } from "@/lib/rfq/contract";
import { fileName, fileTag, usePackageFiles } from "@/lib/rfq/package-context";
import { departmentOptions, departmentRole, inDepartment, scopeLine } from "@/lib/rfq/scope";
import { useViewer } from "@/lib/rfq/session";
import { IconClose, IconLock, IconWarn } from "../ui/icons";
import {
  CitationChip,
  DepartmentChips,
  EmptyState,
  ErrorState,
  MaskedLabel,
  OriginLabel,
  Panel,
  PriorityChip,
  RedFlagChip,
  Skeleton,
  cx,
  locusKey,
  sameLocus,
  useLocusLabel,
} from "../ui/primitives";

export type DeptRole = "all" | "primary" | "reference";

const MASKED_ROW_H = 56;

/** DOM-safe, collision-free id for a clause: every character outside [A-Za-z0-9] is hex-escaped. */
export function clauseDomId(clauseId: string): string {
  return `clause-${clauseId.replace(/[^A-Za-z0-9]/g, (ch) => `_${ch.charCodeAt(0).toString(16)}`)}`;
}

/** Title for display. It is empty when the clause has none, or when its heading is out of the account's scope. */
function displayTitle(c: VisibleClause): string {
  return c.title || c.excerpt[0]?.text || "(표시할 제목 없음)";
}

// ── List ─────────────────────────────────────────────────────────────────────
type ListProps = {
  status: "loading" | "ok" | "error";
  error: string | null;
  clauses: ClauseItem[];
  provenance?: ProvenanceMap;
  /** Rule departments: labels of the chips, and the view filter (those of visible clauses only). */
  departments: Department[];
  department: string | null;
  onDepartment: (d: string | null) => void;
  role: DeptRole;
  onRole: (r: DeptRole) => void;
  selected: string | null;
  onSelect: (clauseId: string) => void;
  className?: string;
};

export function ClauseList({
  status,
  error,
  clauses,
  provenance,
  departments,
  department,
  onDepartment,
  role,
  onRole,
  selected,
  onSelect,
  className,
}: ListProps) {
  const viewer = useViewer();
  const files = usePackageFiles();
  const multiFile = files.length > 1;
  const deptLabel = (id: string) => departments.find((d) => d.id === id)?.label ?? id;
  const options = departmentOptions(clauses, departments);
  const dept = options.find((d) => d.id === department) ?? null;

  // Lock rows stay in every view: the filter must not tell whose clause a lock is.
  const inView = clauses.filter((c) => inDepartment(c, dept));
  const shown = inView.filter(
    (c) => c.kind === "masked" || !dept || role === "all" || departmentRole(c, dept.id) === role,
  );
  const visible = shown.filter((c): c is VisibleClause => c.kind === "clause");
  const redFlags = visible.filter((c) => c.red_flag).length;
  const unverified = visible.filter((c) => c.summary.citation_status === "unverified").length;
  const masked = shown.length - visible.length;
  const countRole = (r: "primary" | "reference") =>
    dept ? inView.filter((c) => c.kind === "clause" && departmentRole(c, dept.id) === r).length : 0;

  function onKeyDown(e: KeyboardEvent<HTMLUListElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const ids = shown.map((c) => c.clause_id);
    const idx = selected ? ids.indexOf(selected) : -1;
    const next = e.key === "ArrowDown" ? Math.min(ids.length - 1, idx + 1) : Math.max(0, idx - 1);
    const id = ids[next];
    if (id) {
      onSelect(id);
      document.getElementById(clauseDomId(id))?.focus();
    }
  }

  const pill = (active: boolean) =>
    cx(
      "shrink-0 rounded-full border px-2.5 py-1 text-label font-medium transition-soft",
      active ? "border-ink bg-ink text-white" : "border-line text-ink-muted hover:border-line-control hover:text-ink",
    );

  return (
    <Panel title="부서별 조항" provenance={provenance} labelId="clauses-title" className={className} bodyClassName="flex flex-col">
      {status === "ok" && (
        <p className="border-b border-line px-4 py-2 text-label font-semibold text-ink tabular" data-testid="scope-line">
          {scopeLine(viewer, clauses)}
        </p>
      )}
      {options.length > 0 && (
        <div className="flex items-center gap-2 border-b border-line px-4 py-2">
          <span id="view-filter" className="shrink-0 text-label text-ink-muted">
            보기: 부서
          </span>
          <div className="flex gap-1 overflow-x-auto p-0.5" role="group" aria-labelledby="view-filter">
            {[{ id: "", label: "전체" }, ...options].map((d) => {
              const active = (dept?.id ?? "") === d.id;
              return (
                <button key={d.id || "all"} type="button" aria-pressed={active} onClick={() => onDepartment(d.id || null)} className={pill(active)}>
                  {d.label}
                </button>
              );
            })}
          </div>
        </div>
      )}
      {dept && status === "ok" && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-2 text-label text-ink-muted" data-testid="role-filter">
          {dept.receives_all ? (
            <span>{dept.label}은(는) 모든 조항을 받습니다 · 주관 {countRole("primary")}</span>
          ) : (
            <span className="tabular">
              주관 {countRole("primary")} · 참조 {countRole("reference")}
            </span>
          )}
          <span className="ml-auto flex gap-1" role="group" aria-label="역할 필터">
            {(["all", "primary", "reference"] as const).map((r) => (
              <button key={r} type="button" aria-pressed={role === r} onClick={() => onRole(r)} className={pill(role === r)}>
                {r === "all" ? "전체" : r === "primary" ? "주관만" : "참조만"}
              </button>
            ))}
          </span>
        </div>
      )}
      {status === "ok" && shown.length > 0 && (
        <p
          className="flex flex-wrap gap-x-3 border-b border-line px-4 py-2 text-label text-ink-muted tabular"
          data-testid="clause-summary"
        >
          <span>
            <strong className="font-semibold text-ink">{shown.length}</strong>개 조항
          </span>
          {multiFile && <span>파일 {files.length}개</span>}
          {redFlags > 0 && <span className="text-danger">독소조항 {redFlags}</span>}
          {unverified > 0 && <span className="text-warn">미검증 {unverified}</span>}
          {masked > 0 && (
            <span className="inline-flex items-center gap-1">
              <IconLock size={12} /> 자물쇠 {masked}
            </span>
          )}
        </p>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {status === "loading" && <Skeleton lines={8} />}
        {status === "error" && error && <ErrorState message={error} />}
        {status === "ok" && shown.length === 0 && (
          <EmptyState title="표시할 조항이 없습니다">
            <p>이 보기에 해당하는 조항이 없거나, 분석 결과가 비어 있습니다.</p>
          </EmptyState>
        )}
        {status === "ok" && shown.length > 0 && (
          <ul aria-label="조항 목록" onKeyDown={onKeyDown} data-testid="clause-list">
            {shown.map((c) => {
              const isSelected = selected === c.clause_id;
              const tag = multiFile ? fileTag(files, c.source_doc) : null;
              const number = (
                <span className="w-16 shrink-0 break-all pt-0.5 text-dense font-semibold tabular" title={c.clause_id}>
                  {tag && <span className="mr-1 text-label font-medium text-ink-muted">{tag}</span>}
                  {c.section_id}
                </span>
              );
              if (c.kind === "masked") {
                return (
                  <li key={c.clause_id} style={{ minHeight: MASKED_ROW_H }} className="border-b border-line">
                    <button
                      id={clauseDomId(c.clause_id)}
                      type="button"
                      data-testid="masked-row"
                      data-clause-id={c.clause_id}
                      aria-current={isSelected}
                      onClick={() => onSelect(c.clause_id)}
                      className={cx(
                        "flex w-full items-center gap-3 px-4 py-3 text-left text-ink-muted transition-soft",
                        isSelected ? "bg-canvas" : "hover:bg-canvas",
                      )}
                      style={{ minHeight: MASKED_ROW_H }}
                    >
                      {number}
                      <MaskedLabel />
                    </button>
                  </li>
                );
              }
              const isUnverified = c.summary.citation_status === "unverified";
              return (
                <li key={c.clause_id} className="border-b border-line">
                  <button
                    id={clauseDomId(c.clause_id)}
                    type="button"
                    aria-current={isSelected}
                    onClick={() => onSelect(c.clause_id)}
                    data-testid="clause-row"
                    data-clause-id={c.clause_id}
                    className={cx(
                      "relative flex w-full gap-3 px-4 py-3 text-left text-ink transition-soft",
                      isSelected ? "bg-accent-tint" : "hover:bg-canvas",
                    )}
                  >
                    {isSelected && <span aria-hidden="true" className="absolute inset-y-0 left-0 w-0.5 bg-accent" />}
                    {number}
                    <span className="flex min-w-0 flex-1 flex-col gap-1.5">
                      <span className={cx("line-clamp-2 text-dense font-medium text-ink", !c.title && "font-serif font-normal")}>
                        {displayTitle(c)}
                      </span>
                      <span className="flex flex-wrap items-center gap-1">
                        <PriorityChip priority={c.priority} />
                        {c.red_flag && <RedFlagChip label={c.red_flag.label} />}
                        {isUnverified && (
                          <span className="inline-flex items-center gap-1 rounded-chip border border-warn/40 bg-warn-tint px-1.5 py-0.5 text-label font-medium text-warn">
                            <IconWarn size={12} /> 미검증
                          </span>
                        )}
                      </span>
                      <DepartmentChips primary={c.primary_department} refs={c.reference_departments} label={deptLabel} />
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Panel>
  );
}

// ── Detail ───────────────────────────────────────────────────────────────────
type DetailProps = {
  clause: ClauseItem | null;
  provenance?: ProvenanceMap;
  departments: Department[];
  target: Locus | null;
  onCite: (l: Locus) => void;
  onClose?: () => void;
  className?: string;
  /** Unique per mount: the workspace renders this panel twice (docked and floating). */
  idPrefix?: string;
};

export function ClauseDetail({
  clause,
  provenance,
  departments,
  target,
  onCite,
  onClose,
  className,
  idPrefix = "detail",
}: DetailProps) {
  const files = usePackageFiles();
  const locusLabel = useLocusLabel();
  const [showTranslation, setShowTranslation] = useState(true);
  const deptLabel = (id: string) => departments.find((d) => d.id === id)?.label ?? id;

  const closeButton = onClose && (
    <button
      type="button"
      onClick={onClose}
      aria-label="조항 상세 닫기"
      className="hidden rounded-control p-1 text-ink-muted hover:bg-canvas hover:text-ink lg:inline-flex xl:hidden"
    >
      <IconClose size={16} />
    </button>
  );

  if (!clause) {
    return (
      <Panel title="조항 상세" labelId={`${idPrefix}-title`} as="aside" className={className} actions={closeButton}>
        <EmptyState title="조항을 고르세요">
          <p>목록에서 조항을 고르면 요약, 원문 발췌, 인용이 여기에 나옵니다.</p>
        </EmptyState>
      </Panel>
    );
  }

  if (clause.kind === "masked") {
    return (
      <Panel title={`${clause.section_id} 조항`.trim()} labelId={`${idPrefix}-title`} as="aside" className={className} actions={closeButton}>
        <div className="flex flex-col gap-2 p-4" data-testid="masked-detail">
          <MaskedLabel />
          <p className="text-label text-ink-muted">{fileName(files, clause.source_doc)}</p>
          <p className="text-dense text-ink-muted">
            이 계정의 열람 범위로는 원문, 요약, 수치를 볼 수 없습니다.
          </p>
        </div>
      </Panel>
    );
  }

  const s = clause.summary;
  const verified = s.citation_status === "verified";
  const hasSummary = s.finding.text.trim().length > 0;
  const excerptLang = clause.lang === "en" || clause.lang === "de" || clause.lang === "ko" ? clause.lang : undefined;

  return (
    <Panel
      title={`${clause.section_id} ${clause.title}`.trim() || "조항 상세"}
      labelId={`${idPrefix}-title`}
      as="aside"
      provenance={provenance}
      className={className}
      bodyClassName="overflow-y-auto"
      actions={closeButton}
    >
      <div className="flex flex-col gap-5 p-4" data-testid="clause-detail">
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <PriorityChip priority={clause.priority} />
            {clause.red_flag && <RedFlagChip label={clause.red_flag.label} />}
            <OriginLabel origin={clause.origin} />
          </div>
          <DepartmentChips primary={clause.primary_department} refs={clause.reference_departments} label={deptLabel} maxRefs={8} />
          {files.length > 1 && (
            <p className="text-label text-ink-muted" data-testid="clause-file">
              {fileTag(files, clause.source_doc)} · {fileName(files, clause.source_doc)}
            </p>
          )}
        </div>

        <section aria-labelledby={`${idPrefix}-summary`} className="flex flex-col gap-2">
          <h3 id={`${idPrefix}-summary`} className="text-label font-semibold text-ink-muted">
            요약
          </h3>
          {hasSummary ? (
            <p className="text-body text-ink">{s.finding.text}</p>
          ) : (
            <p className="text-dense text-ink-muted" data-testid="no-summary">
              {clause.origin === "rule" || clause.origin === "rule_fallback"
                ? "요약 없음 (LLM 미연결). 판정은 규칙으로 했고, 근거는 아래 원문 발췌입니다."
                : "요약이 비어 있습니다. 아래 원문 발췌를 확인하세요."}
            </p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {s.finding.citations.map((l, i) => (
              <CitationChip key={locusKey(l, i)} locus={l} verified={verified} onOpen={onCite} isActive={sameLocus(target, l)} />
            ))}
          </div>
          {!verified && (
            <div
              role="note"
              data-testid="unverified-note"
              className="flex flex-col gap-1 rounded-control border border-warn/40 bg-warn-tint px-3 py-2 text-label text-warn"
            >
              <p className="flex items-center gap-1.5 font-semibold">
                <IconWarn size={14} /> 인용 미검증
              </p>
              <p className="text-ink">
                이 요약의 근거 중 원문에서 확인되지 않은 위치가 있습니다. 결과를 그대로 쓰지 말고 원문을 확인하세요.
              </p>
              {s.failed_citations.map((l, i) => (
                <span key={locusKey(l, i)} className="tabular">
                  확인 실패: {locusLabel(l)}
                </span>
              ))}
            </div>
          )}
        </section>

        {clause.red_flag && (
          <section aria-labelledby={`${idPrefix}-flag`} className="flex flex-col gap-1 border-l-2 border-danger pl-3">
            <h3 id={`${idPrefix}-flag`} className="text-label font-semibold text-danger">
              독소조항 사유
            </h3>
            <p className="text-dense text-ink">{clause.red_flag.reason}</p>
          </section>
        )}

        <section aria-labelledby={`${idPrefix}-excerpt`} className="flex flex-col gap-2">
          <h3 id={`${idPrefix}-excerpt`} className="text-label font-semibold text-ink-muted">
            원문 발췌
          </h3>
          <ol className="flex flex-col gap-2 rounded-control border border-line bg-surface-source p-3">
            {clause.excerpt.map((e, i) => (
              <li key={locusKey(e.locus, i)} lang={excerptLang} className="flex flex-col gap-1">
                <p className="whitespace-pre-line font-serif text-source text-ink">{e.text}</p>
                <button
                  type="button"
                  onClick={() => onCite(e.locus)}
                  lang="ko"
                  className={cx(
                    "self-start text-label tabular underline-offset-2 transition-soft hover:underline",
                    sameLocus(target, e.locus) ? "font-semibold text-accent" : "text-ink-muted",
                  )}
                >
                  {locusLabel(e.locus)}
                </button>
              </li>
            ))}
          </ol>
        </section>

        <section aria-labelledby={`${idPrefix}-tr`} className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <h3 id={`${idPrefix}-tr`} className="text-label font-semibold text-ink-muted">
              국문 참고 번역
            </h3>
            {clause.translation_ko && (
              <>
                <span className="text-label text-ink-muted">구속력 없음</span>
                <button
                  type="button"
                  aria-expanded={showTranslation}
                  onClick={() => setShowTranslation((v) => !v)}
                  className="ml-auto text-label text-accent hover:underline"
                >
                  {showTranslation ? "접기" : "펼치기"}
                </button>
              </>
            )}
          </div>
          {clause.translation_ko ? (
            showTranslation && <p className="text-dense text-ink">{clause.translation_ko}</p>
          ) : (
            <p className="text-label text-ink-muted" data-testid="no-translation">
              번역 없음. 번역 제공자가 연결되지 않아 원문만 제공합니다.
            </p>
          )}
        </section>
      </div>
    </Panel>
  );
}
