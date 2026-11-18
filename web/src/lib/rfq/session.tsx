"use client";
/**
 * Client session: selected team (v1 identity = team picker, SPEC §7) and
 * presentation mode. URL parameters win over the stored session so a link
 * opens the same team view.
 *
 * Implemented as a tiny external store read with useSyncExternalStore: the
 * server snapshot is the neutral initial state (restricted group), and the
 * browser snapshot comes from sessionStorage + URL.
 */
import { createContext, useContext, useEffect, useMemo, useSyncExternalStore } from "react";
import type { ReactNode } from "react";

type Stored = { team: string | null; present: boolean };

const KEY = "autorfx.session";
const INITIAL: Stored = { team: null, present: false };

function readStored(): Stored {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return INITIAL;
    const parsed = JSON.parse(raw) as Partial<Stored>;
    return {
      team: typeof parsed.team === "string" ? parsed.team : null,
      present: parsed.present === true,
    };
  } catch {
    return INITIAL;
  }
}

function readUrl(base: Stored): Stored {
  const q = new URLSearchParams(window.location.search);
  const next = { ...base };
  if (q.has("team")) next.team = q.get("team") || null;
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
};

const SessionContext = createContext<SessionValue | null>(null);

const actions = {
  setTeam: (team: string | null) => update({ team }),
  setPresent: (present: boolean) => update({ present }),
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
