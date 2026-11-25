"use client";
/** The chat log. It scrolls to the newest turn when a question is sent and when its answer arrives. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ErrorState } from "@/components/ui/primitives";
import type { Turn } from "@/lib/rfq/chat-history";
import type { Locus } from "@/lib/rfq/contract";
import { Answer } from "./answer";
import { PANEL_ATTRIBUTE, createDraftTrace } from "./draft-trace";
import { Waiting } from "./waiting";

type ConversationProps = {
  turns: Turn[];
  target: Locus | null;
  onCite: (l: Locus) => void;
  /** Shown while there are no turns. */
  empty: ReactNode;
};

export function Conversation({ turns, target, onCite, empty }: ConversationProps) {
  const newest = useRef<HTMLLIElement>(null);
  const [trace] = useState(createDraftTrace);
  useEffect(() => {
    // "nearest": a short turn is shown whole, a turn taller than the log from its question on.
    newest.current?.scrollIntoView({ block: "nearest" });
  }, [turns]);

  return (
    // The log is the only place the dots of an answer may be drawn in (see dot-origin.ts).
    <ol
      className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4"
      aria-live="polite"
      data-testid="chat-log"
      {...{ [PANEL_ATTRIBUTE]: "" }}
    >
      {turns.length === 0 && empty}
      {turns.map((t, i) => (
        // Below 1024px the page itself scrolls, under the sticky top bar.
        <li
          key={i}
          ref={i === turns.length - 1 ? newest : undefined}
          className="flex scroll-mt-36 flex-col gap-2 lg:scroll-mt-4"
        >
          <p className="max-w-[85%] self-end rounded-panel bg-ink px-3 py-2 text-dense text-white">{t.question}</p>
          {!t.response && !t.error && <Waiting startedAt={t.startedAt} progress={t.progress} trace={trace} />}
          {t.error && <ErrorState message={t.error} />}
          {t.response && <Answer response={t.response} turnId={t.startedAt} trace={trace} target={target} onCite={onCite} />}
        </li>
      ))}
    </ol>
  );
}
