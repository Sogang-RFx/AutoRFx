"use client";
/** The microphone button, the "답변 읽어 주기" switch and the line that says what the voice is doing. */
import { IconMic, IconStop } from "@/components/ui/icons";
import { FormMessage, cx } from "@/components/ui/primitives";
import type { VoicePhase, VoiceState } from "@/lib/voice/state";

const MIC_LABEL: Record<VoicePhase, string> = {
  idle: "음성으로 질문하기",
  asking_permission: "마이크 허용을 기다리는 중, 누르면 취소합니다",
  recording: "녹음을 끝내고 질문 보내기",
  transcribing: "말씀을 글로 바꾸는 중",
  speaking: "음성으로 질문하기, 읽기는 멈춥니다",
  error: "음성으로 질문하기",
};

export function MicButton({ phase, onPress }: { phase: VoicePhase; onPress: () => void }) {
  const isRecording = phase === "recording";
  return (
    <button
      type="button"
      onClick={onPress}
      disabled={phase === "transcribing"}
      aria-label={MIC_LABEL[phase]}
      data-testid="chat-mic"
      data-phase={phase}
      className={cx(
        "inline-flex min-h-10 min-w-10 items-center justify-center rounded-control border transition-soft",
        "disabled:cursor-not-allowed disabled:opacity-50",
        isRecording
          ? "border-ink bg-ink text-white hover:bg-ink-muted"
          : "border-line-control bg-surface text-ink hover:bg-canvas",
      )}
    >
      {isRecording ? <IconStop size={16} /> : <IconMic size={16} />}
    </button>
  );
}

/** Same look as the "화면 효과" switch of the account menu. */
export function ReadAloudSwitch({ on, onChange }: { on: boolean; onChange: (on: boolean) => void }) {
  return (
    <label className="inline-flex items-center gap-2 text-label text-ink-muted">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        data-testid="chat-read-aloud"
        onClick={() => onChange(!on)}
        className={cx(
          "relative h-5 w-9 shrink-0 rounded-full border transition-soft",
          on ? "border-ink bg-ink" : "border-line-control bg-surface",
        )}
      >
        <span
          aria-hidden="true"
          className={cx(
            "absolute top-0.5 h-3.5 w-3.5 rounded-full transition-soft",
            on ? "left-[18px] bg-white" : "left-0.5 bg-line-control",
          )}
        />
      </button>
      답변 읽어 주기
    </label>
  );
}

const clock = (seconds: number): string => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

type StatusProps = {
  state: VoiceState;
  recordedSeconds: number;
  maxSeconds: number;
  errorMessage: string | null;
  onStopSpeaking: () => void;
};

/**
 * The words stay still for screen readers (a status region); the running clock is hidden from them, since a
 * live region would read every second.
 */
export function VoiceStatus({ state, recordedSeconds, maxSeconds, errorMessage, onStopSpeaking }: StatusProps) {
  if (state.phase === "error") return <FormMessage tone="error">{errorMessage}</FormMessage>;
  return (
    <p role="status" className="min-h-5 text-label text-ink-muted empty:hidden" data-testid="chat-voice-status">
      {state.phase === "asking_permission" && "브라우저가 마이크 사용을 묻고 있습니다. 허용해 주세요."}
      {state.phase === "recording" && (
        <>
          <span className="font-medium text-ink">녹음 중</span>
          <span aria-hidden="true" className="tabular">
            {" "}
            · {clock(recordedSeconds)} / {clock(maxSeconds)}
          </span>
          {" · 다시 누르면 보내고, Esc를 누르면 취소합니다."}
        </>
      )}
      {state.phase === "transcribing" && "말씀을 글로 바꾸는 중…"}
      {state.phase === "speaking" && (
        <>
          답변을 읽는 중{" "}
          <button type="button" onClick={onStopSpeaking} className="underline underline-offset-2 hover:text-ink">
            멈추기
          </button>
        </>
      )}
    </p>
  );
}
