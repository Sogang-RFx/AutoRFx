"use client";
/** Suggested questions and the question field of the chat, with the voice controls when the server offers them. */
import { IconArrowRight } from "@/components/ui/icons";
import { Button, cx } from "@/components/ui/primitives";
import { LevelOrb } from "@/components/voice/level-orb";
import type { Voice, VoiceConfig } from "@/components/voice/use-voice";
import { MicButton, ReadAloudSwitch, VoiceStatus } from "@/components/voice/voice-controls";
import { MAX_QUESTION_CHARS } from "@/lib/rfq/chat-history";

const COMPOSER_ORB_SIZE = 40;
const ACTIVE_PHASES = ["recording", "transcribing", "speaking"];

type ComposerProps = {
  /** Follow-up questions shown above the field; empty while the conversation has not begun. */
  suggestions: string[];
  hasTurns: boolean;
  isBusy: boolean;
  draft: string;
  onDraftChange: (text: string) => void;
  /** True when the question was taken; false when the screen kept it for the input (too long, answer pending). */
  onAsk: (question: string) => boolean;
  /** Changes with each stream event of the pending turn. */
  pulseKey: number;
  voice: Voice;
  voiceConfig: VoiceConfig | null;
  /** A line about what the voice did with the speech (for example, a transcript too long to send). */
  notice: string | null;
};

export function Composer({
  suggestions,
  hasTurns,
  isBusy,
  draft,
  onDraftChange,
  onAsk,
  pulseKey,
  voice,
  voiceConfig,
  notice,
}: ComposerProps) {
  function submit(text: string) {
    if (!text.trim() || isBusy) return;
    if (onAsk(text)) onDraftChange("");
  }

  const hasVoice = voice.canListen || voice.canSpeak;
  // With no turns yet the big sphere of the empty state is the one that follows the voice.
  const showOrb = (ACTIVE_PHASES.includes(voice.state.phase) || isBusy) && hasTurns;

  return (
    <>
      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-line px-4 pt-3">
          {suggestions.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => submit(s)}
              disabled={isBusy}
              className="rounded-full border border-line px-2.5 py-1 text-label text-ink-muted transition-soft hover:border-line-control hover:text-ink disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(draft);
        }}
        className={cx("flex flex-col gap-2 p-4", suggestions.length === 0 && "border-t border-line")}
      >
        <div className="flex items-center gap-2">
          {showOrb && (
            <LevelOrb
              phase={voice.state.phase}
              isBusy={isBusy}
              level={voice.level}
              pulseKey={pulseKey}
              size={COMPOSER_ORB_SIZE}
              className="shrink-0"
            />
          )}
          <label className="sr-only" htmlFor="chat-input">
            질문
          </label>
          <input
            id="chat-input"
            value={draft}
            onChange={(e) => onDraftChange(e.target.value)}
            maxLength={MAX_QUESTION_CHARS}
            placeholder="예: 인도조건은 무엇인가요?"
            className="min-h-10 min-w-0 flex-1 rounded-control border border-line-control bg-surface px-3 text-body text-ink"
            data-testid="chat-input"
          />
          {voice.canListen && <MicButton phase={voice.state.phase} onPress={voice.toggleMic} />}
          <Button type="submit" variant="primary" disabled={!draft.trim() || isBusy} aria-label="질문 보내기">
            <IconArrowRight size={16} />
          </Button>
        </div>
        {hasVoice && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {voice.canSpeak && <ReadAloudSwitch on={voice.readAloud} onChange={voice.setReadAloud} />}
            <div className="min-w-0 flex-1">
              <VoiceStatus
                state={voice.state}
                recordedSeconds={voice.recordedSeconds}
                maxSeconds={voiceConfig?.max_seconds ?? 0}
                errorMessage={voice.errorMessage}
                onStopSpeaking={voice.stopSpeaking}
              />
            </div>
          </div>
        )}
        {notice && (
          <p role="status" className="text-label text-ink-muted" data-testid="chat-voice-notice">
            {notice}
          </p>
        )}
      </form>
    </>
  );
}
