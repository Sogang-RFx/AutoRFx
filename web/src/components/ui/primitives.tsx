"use client";
/**
 * Base components (TASKS S008, SPEC §15.3). State is never shown by colour
 * alone: every chip also carries a word and/or an icon.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { Engines, Locus, Origin, Priority, ProvenanceMap } from "@/lib/rfq/contract";
import { fileName, fileTag, usePackageFiles } from "@/lib/rfq/package-context";
import { translationLabel } from "@/lib/rfq/translation";
import { IconCheck, IconFlag, IconLock, IconWarn } from "./icons";

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

// ── Chips ────────────────────────────────────────────────────────────────────
const chipBase =
  "inline-flex items-center gap-1 rounded-chip px-1.5 py-0.5 text-label font-medium whitespace-nowrap tabular";

export const PRIORITY_LABEL: Record<Priority, string> = {
  must: "Must",
  should: "Should",
  optional: "Optional",
  info: "Info",
};

/** Weight, not colour: filled → outlined → light outline → bare text. */
export function PriorityChip({ priority }: { priority: Priority }) {
  const style: Record<Priority, string> = {
    must: "bg-ink text-white border border-ink",
    should: "border border-ink text-ink",
    optional: "border border-line text-ink-muted",
    info: "text-ink-muted px-0",
  };
  return (
    <span className={cx(chipBase, style[priority])} aria-label={`요건 강도 ${PRIORITY_LABEL[priority]}`}>
      {PRIORITY_LABEL[priority]}
    </span>
  );
}

/** Type labels come from the rule files through the response, never from code. */
export function RedFlagChip({ label }: { label: string }) {
  return (
    <span className={cx(chipBase, "border border-danger/40 bg-danger-tint text-danger")}>
      <IconFlag size={12} />
      독소조항 · {label}
    </span>
  );
}

/**
 * Lead department as an outlined bold chip, reference departments as quiet
 * text (weight and words, not colour). Up to `maxRefs` references, then "외 n".
 */
export function DepartmentChips({
  primary,
  refs,
  label,
  maxRefs = 3,
}: {
  primary: string;
  refs: string[];
  label: (id: string) => string;
  maxRefs?: number;
}) {
  const shown = refs.slice(0, maxRefs);
  const rest = refs.length - shown.length;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className={cx(chipBase, "border border-ink font-semibold text-ink")} data-testid="primary-department">
        주관 {label(primary)}
      </span>
      {shown.length > 0 && (
        <span className="text-label text-ink-muted">
          참조 {shown.map(label).join(", ")}
          {rest > 0 && ` 외 ${rest}`}
        </span>
      )}
    </span>
  );
}

/** "A · 2페이지 7번째 문장 · Sheet!C6": file tag, page, sentence and the cell when there is one. */
export function formatLocus(l: Pick<Locus, "page_number" | "sentence_index" | "anchor">, tag?: string): string {
  const base = `${l.page_number}페이지 ${l.sentence_index}번째 문장`;
  return [tag, base, l.anchor].filter(Boolean).join(" · ");
}

/** Same position: file, page and sentence. */
export function sameLocus(a: Locus | null, b: Locus): boolean {
  return Boolean(
    a && a.source_doc === b.source_doc && a.page_number === b.page_number && a.sentence_index === b.sentence_index,
  );
}

export function locusKey(l: Locus, index: number): string {
  return `${l.source_doc}-${l.page_number}-${l.sentence_index}-${index}`;
}

/** formatLocus with the file tag of the open package. */
export function useLocusLabel(): (l: Locus) => string {
  const files = usePackageFiles();
  return (l) => formatLocus(l, files.length > 1 ? fileTag(files, l.source_doc) : undefined);
}

type CitationChipProps = {
  locus: Locus;
  verified: boolean;
  onOpen?: (locus: Locus) => void;
  isActive?: boolean;
};

/** Citation colour follows verification state (SPEC §15.1). The file name is in the tooltip. */
export function CitationChip({ locus, verified, onOpen, isActive }: CitationChipProps) {
  const files = usePackageFiles();
  const label = useLocusLabel()(locus);
  const file = fileName(files, locus.source_doc);
  const tone = verified
    ? "bg-ok-tint text-ok border border-ok/35 hover:border-ok"
    : "bg-warn-tint text-warn border border-warn/40 hover:border-warn";
  const content = (
    <>
      {verified ? <IconCheck size={12} /> : <IconWarn size={12} />}
      {label}
      {!verified && <span className="font-semibold">· 미검증</span>}
    </>
  );
  if (!onOpen) {
    return (
      <span className={cx(chipBase, tone)} title={file}>
        {content}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onOpen(locus)}
      aria-pressed={isActive}
      title={file}
      aria-label={`원문 열기: ${file} ${label}${verified ? "" : " (미검증)"}`}
      className={cx(chipBase, tone, "max-w-full cursor-pointer truncate transition-soft", isActive && "outline-2 outline-accent")}
    >
      {content}
    </button>
  );
}

/** How the results were computed: rule files only, or an LLM. */
export function engineLabel(engines: Engines): string | null {
  if (engines.analysis === "rule-only" || !engines.llm_connected) return "규칙 기반 · LLM 미연결";
  return `LLM 분석 · ${engines.analysis.replace(/^openai:/, "")}`;
}

export function EngineBadge({ engines }: { engines: Engines }) {
  const label = engineLabel(engines);
  if (!label) return null;
  const translation = translationLabel(engines.translation);
  const title = `분석: ${engines.analysis} · 번역: ${translation} · 검색: ${engines.retrieval}`;
  return (
    <span
      data-testid="engine-badge"
      title={title}
      className={cx(chipBase, "border border-line-control bg-surface py-1 font-semibold text-ink")}
    >
      <IconWarn size={12} className="text-ink-muted" />
      {label}
      <span className="sr-only">. {title}</span>
    </span>
  );
}

const ORIGIN_LABEL: Record<Origin, string> = {
  fixture: "출처 확인 안 됨",
  rule: "규칙으로 판정",
  llm: "LLM으로 판정",
  rule_fallback: "LLM 응답 실패, 규칙으로 판정",
};

export function OriginLabel({ origin }: { origin: Origin }) {
  return (
    <span className="text-label text-ink-muted" data-testid="clause-origin">
      {ORIGIN_LABEL[origin]}
    </span>
  );
}

export function MaskedLabel() {
  return (
    <span className="inline-flex items-center gap-1.5 text-dense text-ink-muted">
      <IconLock size={14} />이 팀에는 표시되지 않음
    </span>
  );
}

/** Shows that a panel's data came from the backend. The provider refuses anything else (INV-10). */
export function ProvenanceBadge({ provenance }: { provenance: ProvenanceMap | undefined }) {
  if (!provenance || Object.keys(provenance).length === 0) return null;
  return <span className={cx(chipBase, "border border-line text-ink-muted")}>실제 데이터</span>;
}

// ── Layout pieces ────────────────────────────────────────────────────────────
type PanelProps = {
  title: ReactNode;
  provenance?: ProvenanceMap;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  as?: "section" | "aside" | "div";
  labelId?: string;
};

export function Panel({
  title,
  provenance,
  actions,
  children,
  className,
  bodyClassName,
  as = "section",
  labelId,
}: PanelProps) {
  const Tag = as;
  return (
    <Tag
      aria-labelledby={labelId}
      className={cx("flex min-h-0 min-w-0 flex-col rounded-panel border border-line bg-surface", className)}
    >
      <header className="flex min-h-11 flex-wrap items-center gap-2 border-b border-line px-4 py-2">
        <h2 id={labelId} className="min-w-0 truncate text-dense font-semibold text-ink">
          {title}
        </h2>
        <ProvenanceBadge provenance={provenance} />
        {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
      </header>
      <div className={cx("min-h-0 flex-1", bodyClassName)}>{children}</div>
    </Tag>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" };

export function Button({ variant = "secondary", className, type = "button", ...rest }: ButtonProps) {
  const styles = {
    primary: "bg-accent text-white border border-accent hover:bg-ink hover:border-ink",
    secondary: "bg-surface text-ink border border-line-control hover:bg-canvas",
    ghost: "text-ink-muted border border-transparent hover:text-ink hover:bg-canvas",
  } as const;
  return (
    <button
      type={type}
      className={cx(
        "inline-flex min-h-9 items-center justify-center gap-2 rounded-control px-3 text-dense font-medium transition-soft",
        "disabled:cursor-not-allowed disabled:opacity-50",
        styles[variant],
        className,
      )}
      {...rest}
    />
  );
}

// ── States ───────────────────────────────────────────────────────────────────
/** Appears only after 150ms so fast responses do not flicker (SPEC §15.5). */
export function Skeleton({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cx("skeleton-delay space-y-2 p-4", className)} aria-busy="true" aria-label="불러오는 중">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="pulse-soft h-3 rounded-chip bg-canvas" style={{ width: `${88 - ((i * 17) % 40)}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-1 p-6 text-dense text-ink-muted" role="status">
      <p className="text-body font-semibold text-ink">{title}</p>
      {children}
    </div>
  );
}

/** A 403: the team may not see this. A lock, not an error. */
export function ForbiddenState({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 p-6 text-dense text-ink-muted" role="status" data-testid="forbidden">
      <IconLock size={16} className="mt-0.5 shrink-0" />
      <p>{message}</p>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="m-4 flex flex-col gap-2 rounded-control border border-danger/40 bg-danger-tint p-4 text-dense text-danger"
    >
      <p className="flex items-center gap-2 font-semibold">
        <IconWarn size={16} />
        불러오지 못했습니다
      </p>
      <p className="text-ink">{message}</p>
      {onRetry && (
        <Button variant="secondary" onClick={onRetry} className="self-start">
          다시 시도
        </Button>
      )}
    </div>
  );
}
