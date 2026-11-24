"use client";
/** The chat log. It scrolls to the newest turn when a question is sent and when its answer arrives. */
import { useEffect, useRef, useState } from "react";
import { ErrorState } from "@/components/ui/primitives";
import type { Turn } from "@/lib/rfq/chat-history";
import type { Locus } from "@/lib/rfq/contract";
import { Answer } from "./answer";

/** The seconds are hidden from screen readers: the log is a live region and would read every tick. */
function Waiting({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(startedAt);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  return (
    <p className="text-label text-ink-muted" data-testid="chat-waiting">
      <span className="pulse-soft">답을 찾는 중…</span> 10초 정도 걸릴 수 있습니다
      <span aria-hidden="true" className="tabular">
        {" "}
        · {seconds}초 지남
      </span>
    </p>
  );
}

type ConversationProps = { turns: Turn[]; target: Locus | null; onCite: (l: Locus) => void };

export function Conversation({ turns, target, onCite }: ConversationProps) {
  const newest = useRef<HTMLLIElement>(null);
  useEffect(() => {
    // "nearest": a short turn is shown whole, a turn taller than the log from its question on.
    newest.current?.scrollIntoView({ block: "nearest" });
  }, [turns]);

  return (
    <ol className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4" aria-live="polite" data-testid="chat-log">
      {turns.length === 0 && (
        <li className="text-dense text-ink-muted">답은 원문 문장을 인용해서만 하고, 근거가 없으면 답하지 않습니다.</li>
      )}
      {turns.map((t, i) => (
        // Below 1024px the page itself scrolls, under the sticky top bar.
        <li
          key={i}
          ref={i === turns.length - 1 ? newest : undefined}
          className="flex scroll-mt-36 flex-col gap-2 lg:scroll-mt-4"
        >
          <p className="max-w-[85%] self-end rounded-panel bg-ink px-3 py-2 text-dense text-white">{t.question}</p>
          {!t.response && !t.error && <Waiting startedAt={t.startedAt} />}
          {t.error && <ErrorState message={t.error} />}
          {t.response && <Answer response={t.response} target={target} onCite={onCite} />}
        </li>
      ))}
    </ol>
  );
}
