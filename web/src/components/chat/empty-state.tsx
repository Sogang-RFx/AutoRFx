"use client";
/** The empty conversation: the sphere, the rule of the chat in one line, and the questions to start with. */
import { LevelOrb } from "@/components/voice/level-orb";
import type { LevelSource } from "@/lib/voice/level-store";
import type { VoicePhase } from "@/lib/voice/state";

const ORB_SIZE = 96;

type EmptyStateProps = {
  orb: { phase: VoicePhase; isBusy: boolean; level: LevelSource; pulseKey: number };
  starters: string[];
  isBusy: boolean;
  onAsk: (question: string) => void;
};

export function EmptyState({ orb, starters, isBusy, onAsk }: EmptyStateProps) {
  return (
    <li className="flex flex-col items-center gap-3 py-4 text-center" data-testid="chat-empty">
      <LevelOrb {...orb} size={ORB_SIZE} />
      <p className="text-dense text-ink-muted">답은 원문 문장을 인용해서만 하고, 근거가 없으면 답하지 않습니다.</p>
      <ul className="flex flex-wrap justify-center gap-1.5">
        {starters.map((s) => (
          <li key={s}>
            <button
              type="button"
              onClick={() => onAsk(s)}
              disabled={isBusy}
              className="rounded-full border border-line px-2.5 py-1 text-label text-ink-muted transition-soft hover:border-line-control hover:text-ink disabled:opacity-50"
            >
              {s}
            </button>
          </li>
        ))}
      </ul>
    </li>
  );
}
