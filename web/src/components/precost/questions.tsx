/** Knockout questions and the citations beside them. */
import type { RequirementCheck, VerifiedFinding } from "@/lib/rfq/contract";
import type { Answer } from "@/lib/rfq/precost-form";
import { CitationChip, cx, locusKey } from "../ui/primitives";

export function KnockoutCount({ asked, settled }: { asked: number; settled: number }) {
  return (
    <span className="text-label text-ink-muted tabular">
      질문 {asked} · 자동 확인 {settled}
    </span>
  );
}

export function Cite({ evidence }: { evidence: VerifiedFinding | null }) {
  if (!evidence) return null;
  const verified = evidence.citation_status === "verified";
  return (
    <>
      {evidence.finding.citations.map((l, i) => (
        <CitationChip key={locusKey(l, i)} locus={l} verified={verified} />
      ))}
    </>
  );
}

export const ANSWERS: { value: Answer; label: string }[] = [
  { value: "yes", label: "예" },
  { value: "no", label: "아니오" },
  { value: "unknown", label: "모름" },
];

export function Question({
  r,
  department,
  remembered,
  value,
  onChange,
}: {
  r: RequirementCheck;
  department: string;
  /** A fact answered by this account is kept for the next RFQ. */
  remembered: boolean;
  value: Answer | undefined;
  onChange: (a: Answer) => void;
}) {
  return (
    <li className="flex flex-col gap-1.5 rounded-control border border-line p-3" data-testid={`question-${r.key}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-dense font-medium text-ink">{r.question}</span>
        <span className="rounded-chip border border-line px-1.5 text-label text-ink-muted">
          {r.kind === "fact" ? remembered ? "사실 · 저장됨" : "사실" : "판단 · 매번"}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div role="radiogroup" aria-label={r.question} className="flex gap-1">
          {ANSWERS.map((a) => (
            <button
              key={a.value}
              type="button"
              role="radio"
              aria-checked={value === a.value}
              data-testid={`answer-${r.key}-${a.value}`}
              onClick={() => onChange(a.value)}
              className={cx(
                "min-h-8 rounded-control border px-3 text-dense font-medium transition-soft",
                value === a.value
                  ? a.value === "no"
                    ? "border-danger bg-danger-tint text-danger"
                    : a.value === "yes"
                      ? "border-ok bg-ok-tint text-ok"
                      : "border-ink bg-ink text-white"
                  : "border-line-control text-ink hover:bg-canvas",
              )}
            >
              {a.label}
            </button>
          ))}
        </div>
        <span className="text-label text-ink-muted">참고 부서: {department}</span>
        <Cite evidence={r.evidence} />
      </div>
    </li>
  );
}
