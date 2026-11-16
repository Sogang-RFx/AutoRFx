/**
 * Base components (TASKS S008, SPEC §15.3). State is never shown by colour
 * alone: every chip also carries a word and/or an icon.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { Locus, Priority, ProvenanceMap, RedFlagType } from "@/lib/rfq/contract";
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

export const RED_FLAG_LABEL: Record<RedFlagType, string> = {
  forced_cost_reduction: "CR 강제",
  unlimited_liquidated_damages: "무제한 지체상금",
  ownership_transfer: "소유권 귀속",
  unilateral_termination: "일방적 해지",
};

export function RedFlagChip({ type }: { type: RedFlagType }) {
  return (
    <span className={cx(chipBase, "border border-danger/40 bg-danger-tint text-danger")}>
      <IconFlag size={12} />
      독소조항 · {RED_FLAG_LABEL[type]}
    </span>
  );
}

export function formatLocus(l: Pick<Locus, "page_number" | "sentence_index">): string {
  return `${l.page_number}페이지 ${l.sentence_index}번째 문장`;
}

type CitationChipProps = {
  locus: Locus;
  verified: boolean;
  onOpen?: (locus: Locus) => void;
  isActive?: boolean;
};

/** Citation colour follows verification state (SPEC §15.1). */
export function CitationChip({ locus, verified, onOpen, isActive }: CitationChipProps) {
  const label = formatLocus(locus);
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
  if (!onOpen) return <span className={cx(chipBase, tone)}>{content}</span>;
  return (
    <button
      type="button"
      onClick={() => onOpen(locus)}
      aria-pressed={isActive}
      aria-label={`원문 열기: ${label}${verified ? "" : " (미검증)"}`}
      className={cx(chipBase, tone, "transition-soft cursor-pointer", isActive && "outline-2 outline-accent")}
    >
      {content}
    </button>
  );
}

export function MockChip({ title }: { title?: string }) {
  return (
    <span
      className={cx(chipBase, "mock-outline bg-mock-tint text-mock")}
      title={title ?? "목업 데이터입니다. 실제 분석 결과가 아닙니다."}
      data-testid="mock-chip"
    >
      목업
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

/** Shows where a panel's data came from. Mock cannot be hidden (INV-10). */
export function ProvenanceBadge({ provenance }: { provenance: ProvenanceMap | undefined }) {
  if (!provenance) return null;
  const entries = Object.entries(provenance);
  const mockParts = entries.filter(([, v]) => v === "mock").map(([k]) => k);
  if (mockParts.length > 0) return <MockChip title={`목업 부분: ${mockParts.join(", ")}`} />;
  if (entries.length === 0) return null;
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
      <header className="flex min-h-11 items-center gap-2 border-b border-line px-4 py-2">
        <h2 id={labelId} className="text-dense font-semibold text-ink">
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
