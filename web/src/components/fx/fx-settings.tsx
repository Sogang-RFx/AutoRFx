"use client";
/** "화면 효과" switch and "인트로 다시 보기", shown in the account menu. */
import { useId } from "react";
import { requestIntroReplay, setFxEnabled } from "@/lib/fx/prefs";
import { IconReplay, IconSparkle } from "../ui/icons";
import { cx } from "../ui/primitives";
import { useFxState } from "./use-fx";

export function FxSettings({ onReplay }: { onReplay?: () => void }) {
  const state = useFxState();
  const hintId = useId();
  const hint = state.locked
    ? "주소의 fx=0 때문에 이 화면에서는 꺼져 있습니다."
    : state.enabled && state.reduced
      ? "기기의 움직임 줄이기 설정 때문에 입자는 나오지 않습니다."
      : null;

  return (
    <div className="flex flex-col gap-2 border-t border-line pt-3">
      <div className="flex items-center justify-between gap-3">
        <span className="inline-flex items-center gap-1.5 text-dense text-ink">
          <IconSparkle size={14} className="shrink-0" />
          화면 효과
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={state.enabled}
          aria-label="화면 효과"
          aria-describedby={hint ? hintId : undefined}
          disabled={state.locked}
          data-testid="fx-switch"
          onClick={() => setFxEnabled(!state.enabled)}
          className={cx(
            "relative h-5 w-9 shrink-0 rounded-full border transition-soft disabled:opacity-50",
            state.enabled ? "border-ink bg-ink" : "border-line-control bg-surface",
          )}
        >
          <span
            aria-hidden="true"
            className={cx(
              "absolute top-0.5 h-3.5 w-3.5 rounded-full transition-soft",
              state.enabled ? "left-[18px] bg-white" : "left-0.5 bg-line-control",
            )}
          />
        </button>
      </div>
      {hint && (
        <p id={hintId} className="text-label text-ink-muted">
          {hint}
        </p>
      )}
      <button
        type="button"
        data-testid="intro-replay"
        onClick={() => {
          requestIntroReplay();
          onReplay?.();
        }}
        className="inline-flex min-h-8 items-center gap-1.5 self-start rounded-control text-dense text-ink-muted transition-soft hover:text-ink"
      >
        <IconReplay size={14} className="shrink-0" />
        인트로 다시 보기
      </button>
    </div>
  );
}
