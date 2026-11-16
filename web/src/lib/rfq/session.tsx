"use client";
/**
 * Client session: selected team (v1 identity = team picker, SPEC §7),
 * presentation mode, and demo parameters (`scenario`, `speed`, SPEC §13.4).
 * URL parameters win over the stored session so a demo link is reproducible.
 *
 * Implemented as a tiny external store read with useSyncExternalStore: the
 * server snapshot is the neutral initial state (restricted group), and the
 * browser snapshot comes from sessionStorage + URL.
 */
import { createContext, useContext, useEffect, useMemo, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { allHttp } from "./config";

export const DEMO_SCENARIOS = ["default", "unverified", "empty", "error"] as const;
export type DemoScenario = (typeof DEMO_SCENARIOS)[number];

type Stored = { team: string | null; present: boolean; scenario: DemoScenario; speed: number };

const KEY = "autorfx.session";
const INITIAL: Stored = { team: null, present: false, scenario: "default", speed: 1 };

function readStored(): Stored {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return INITIAL;
    return { ...INITIAL, ...(JSON.parse(raw) as Partial<Stored>) };
  } catch {
    return INITIAL;
  }
}

function readUrl(base: Stored): Stored {
  const q = new URLSearchParams(window.location.search);
  const next = { ...base };
  if (q.has("team")) next.team = q.get("team") || null;
  if (!allHttp()) {
    const scenario = q.get("scenario");
    if (scenario && (DEMO_SCENARIOS as readonly string[]).includes(scenario)) next.scenario = scenario as DemoScenario;
    const speed = Number(q.get("speed"));
    if (q.has("speed") && Number.isFinite(speed) && speed > 0) next.speed = speed;
  }
  if (q.get("present") === "1") next.present = true;
  return next;
}

// ── external store ───────────────────────────────────────────────────────────
let snapshot: Stored | null = null;
const listeners = new Set<() => void>();

function getSnapshot(): Stored {
  if (snapshot === null) snapshot = readUrl(readStored());
  return snapshot;
}
const getServerSnapshot = (): Stored => INITIAL;

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function update(patch: Partial<Stored>): void {
  snapshot = { ...getSnapshot(), ...patch };
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(snapshot));
  } catch {
    /* storage unavailable: session stays in memory only */
  }
  listeners.forEach((l) => l());
}

const subscribeNoop = () => () => {};

// ── context ──────────────────────────────────────────────────────────────────
type SessionValue = Stored & {
  /** False during server render and hydration; data hooks wait for it. */
  ready: boolean;
  setTeam: (team: string | null) => void;
  setPresent: (on: boolean) => void;
  setScenario: (s: DemoScenario) => void;
};

const SessionContext = createContext<SessionValue | null>(null);

const actions = {
  setTeam: (team: string | null) => update({ team }),
  setPresent: (present: boolean) => update({ present }),
  setScenario: (scenario: DemoScenario) => update({ scenario }),
};

export function SessionProvider({ children }: { children: ReactNode }) {
  const state = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const ready = useSyncExternalStore(subscribeNoop, () => true, () => false);

  useEffect(() => {
    document.documentElement.dataset.present = state.present ? "on" : "off";
  }, [state.present]);

  const value = useMemo(() => ({ ...state, ready, ...actions }), [state, ready]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside <SessionProvider>");
  return ctx;
}
