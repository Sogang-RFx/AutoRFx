"use client";
/** The effects switch as React state: `useSyncExternalStore` over lib/fx/prefs. */
import { useSyncExternalStore } from "react";
import { fxPrefs, SERVER_FX_STATE, type FxState } from "@/lib/fx/prefs";

const getServerState = (): FxState => SERVER_FX_STATE;

/** Switch, `?fx=0` lock, reduced-motion and the combined `moves`. Server and first hydration pass: nothing moves. */
export function useFxState(): FxState {
  return useSyncExternalStore(fxPrefs.subscribe, fxPrefs.getState, getServerState);
}

/** The "화면 효과" switch (after `?fx=0`). */
export function useFxEnabled(): boolean {
  return useFxState().enabled;
}

/** True only when effects are on and the system does not ask for reduced motion. */
export function useFxMoves(): boolean {
  return useFxState().moves;
}
