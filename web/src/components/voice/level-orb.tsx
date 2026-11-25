"use client";
/**
 * The sphere of the chat, following the voice. It alone subscribes to the voice level, so the level (up to 20 a
 * second) re-renders this canvas wrapper and nothing above it.
 */
import { useSyncExternalStore } from "react";
import { Orb } from "@/components/fx/orb";
import type { LevelSource } from "@/lib/voice/level-store";
import { orbState } from "@/lib/voice/orb";
import type { VoicePhase } from "@/lib/voice/state";

const NO_LEVEL = (): number => 0;

type LevelOrbProps = {
  phase: VoicePhase;
  /** A question was sent and the answer is being made. */
  isBusy: boolean;
  level: LevelSource;
  /** Changes with each real event of the pending turn; the sphere ripples once per change. */
  pulseKey: number;
  size: number;
  className?: string;
};

export function LevelOrb({ phase, isBusy, level, pulseKey, size, className }: LevelOrbProps) {
  const value = useSyncExternalStore(level.subscribe, level.get, NO_LEVEL);
  const { mode, level: shown } = orbState(phase, value, isBusy);
  return <Orb mode={mode} level={shown} size={size} pulseKey={pulseKey} className={className} />;
}
