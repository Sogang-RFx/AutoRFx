"use client";
/**
 * What the screen shows while an answer is made: the stage, the evidence count when the stream knows it, the
 * seconds, and one row of dots for every statement written so far. No draft text is shown: the stream does not
 * carry any, and a draft is not verified (CITE-01). The log is a live region, so only the stage names are
 * readable by a screen reader; counts and seconds are hidden from it.
 */
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { useFxMoves } from "@/components/fx/use-fx";
import { cx } from "@/components/ui/primitives";
import { STAGE_NAMES, type TurnProgress } from "@/lib/rfq/chat-history";
import type { ChatStage } from "@/lib/rfq/contract";
import { PANEL_SELECTOR, captureRows, type DraftTrace } from "./draft-trace";

const BASE_TRAIL: ChatStage[] = ["plan", "search", "write", "verify"];
/** One row is a statement; more than this many would only push the log down. */
const MAX_DOT_ROWS = 12;

/** The usual way through the stages, and the rewrite when the verification sent the draft back. */
export function stageTrail(stage: ChatStage): ChatStage[] {
  return stage === "rewrite" ? [...BASE_TRAIL, "rewrite"] : BASE_TRAIL;
}

function Trail({ stage }: { stage: ChatStage }) {
  const trail = stageTrail(stage);
  const current = trail.indexOf(stage);
  return (
    <span aria-hidden="true">
      {trail.map((name, i) => (
        <span key={name}>
          {i > 0 && " → "}
          <span className={cx(i === current ? "pulse-soft font-medium text-ink" : i < current ? "text-ink-muted" : "opacity-60")}>
            {STAGE_NAMES[name]}
          </span>
        </span>
      ))}
    </span>
  );
}

/** A row is a dotted rule (flat, no gradient): a statement is drafted, its words are not shown. */
function DotRows({ count, moves, boxRef }: { count: number; moves: boolean; boxRef: RefObject<HTMLDivElement | null> }) {
  if (count <= 0) return null;
  const rows = Math.min(count, MAX_DOT_ROWS);
  return (
    <div
      ref={boxRef}
      aria-hidden="true"
      className="flex max-w-[85%] flex-col gap-2"
      data-testid="chat-drafting"
      data-rows={rows}
    >
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} data-fx={moves ? "in" : undefined} className="h-0 w-full border-b-2 border-dotted border-ink-muted" />
      ))}
    </div>
  );
}

type WaitingProps = {
  startedAt: number;
  progress: TurnProgress;
  /** Where the answer's dots will start; the rows are saved there on every render. */
  trace: DraftTrace;
};

export function Waiting({ startedAt, progress, trace }: WaitingProps) {
  const moves = useFxMoves();
  const rowsRef = useRef<HTMLDivElement>(null);
  // No dependency list: the rows move when the log grows, and the last render before the answer is the one needed.
  useLayoutEffect(() => {
    const rows = rowsRef.current;
    const panel = rows?.closest(PANEL_SELECTOR);
    trace.save(startedAt, rows && panel ? captureRows(rows, panel) : []);
  });
  const [now, setNow] = useState(startedAt);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  const { stage, evidence, drafted } = progress;
  return (
    <div className="flex flex-col gap-2" data-testid="chat-waiting">
      <p className="text-label text-ink-muted">
        {stage ? (
          <>
            <Trail stage={stage} />
            <span className="sr-only">{STAGE_NAMES[stage]} 중</span>
          </>
        ) : (
          <>
            <span className="pulse-soft">답을 찾는 중…</span> 10초 정도 걸릴 수 있습니다
          </>
        )}
        <span aria-hidden="true" className="tabular">
          {evidence !== null && ` · 근거 ${evidence}개`} · {seconds}초 지남
        </span>
      </p>
      <DotRows count={drafted} moves={moves} boxRef={rowsRef} />
    </div>
  );
}
