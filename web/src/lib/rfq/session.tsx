"use client";
/**
 * Client session: the signed-in account (from the backend, docs/auth-rbac.md §6) and presentation
 * mode. The cookie is HttpOnly and no identity is kept in browser storage; sessionStorage holds
 * presentation mode only.
 *
 * Both are tiny external stores read with useSyncExternalStore. The server snapshot is "loading",
 * so nothing of the app is rendered before the backend says who is signed in.
 */
import { createContext, useContext, useEffect, useMemo, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { createAuthStore, type AuthChannel, type AuthMessage, type AuthState } from "./auth-store";
import type { Viewer } from "./contract";
import { readPresent, writePresent } from "./presentation";
import { rfqApi, setAuthListener, startNewSession } from "./provider";

// ── signed-in account ────────────────────────────────────────────────────────
/** Messages between the tabs of this browser; without BroadcastChannel a tab follows on focus. */
function tabChannel(name: string): AuthChannel {
  let channel: BroadcastChannel | null = null;
  return {
    post: (message) => channel?.postMessage(message),
    listen: (receive) => {
      if (typeof BroadcastChannel === "undefined") return () => {};
      const opened = new BroadcastChannel(name);
      opened.onmessage = (e: MessageEvent<AuthMessage>) => receive(e.data);
      channel = opened;
      return () => {
        opened.close();
        if (channel === opened) channel = null;
      };
    },
  };
}

const auth = createAuthStore(
  {
    me: () => rfqApi.me(),
    login: rfqApi.login,
    logout: rfqApi.logout,
    changePassword: rfqApi.changePassword,
    startNewSession,
    setAuthListener,
  },
  tabChannel("autorfx.auth"),
);

const LOADING: AuthState = { status: "loading" };
const getServerAuth = (): AuthState => LOADING;

// ── presentation mode ────────────────────────────────────────────────────────
let present: boolean | null = null;
const presentListeners = new Set<() => void>();

const getPresent = (): boolean => (present ??= readPresent(window.sessionStorage, window.location.search));
const subscribePresent = (cb: () => void) => {
  presentListeners.add(cb);
  return () => presentListeners.delete(cb);
};

function setPresent(on: boolean): void {
  present = on;
  writePresent(window.sessionStorage, on);
  presentListeners.forEach((l) => l());
}

// ── context ──────────────────────────────────────────────────────────────────
type SessionValue = {
  auth: AuthState;
  present: boolean;
  setPresent: (on: boolean) => void;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (current: string, next: string) => Promise<void>;
  /** Asks the backend again who is signed in. */
  retry: () => Promise<void>;
};

const SessionContext = createContext<SessionValue | null>(null);

const actions = {
  setPresent,
  login: auth.login,
  logout: auth.logout,
  changePassword: auth.changePassword,
  retry: () => auth.refresh(),
};

export function SessionProvider({ children }: { children: ReactNode }) {
  const state = useSyncExternalStore(auth.subscribe, auth.getSnapshot, getServerAuth);
  const isPresent = useSyncExternalStore(subscribePresent, getPresent, () => false);

  useEffect(() => {
    const stop = auth.start();
    const check = () => {
      if (document.visibilityState === "visible") void auth.refresh();
    };
    // A page taken from the back/forward cache may belong to an account that signed out since.
    const onPageShow = (e: PageTransitionEvent) => {
      if (e.persisted) void auth.refresh(true);
    };
    window.addEventListener("focus", check);
    window.addEventListener("pageshow", onPageShow);
    document.addEventListener("visibilitychange", check);
    return () => {
      stop();
      window.removeEventListener("focus", check);
      window.removeEventListener("pageshow", onPageShow);
      document.removeEventListener("visibilitychange", check);
    };
  }, []);

  useEffect(() => {
    document.documentElement.dataset.present = isPresent ? "on" : "off";
  }, [isPresent]);

  const value = useMemo(() => ({ auth: state, present: isPresent, ...actions }), [state, isPresent]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside <SessionProvider>");
  return ctx;
}

/** The signed-in account. Only screens behind <AuthGate> may call it. */
export function useViewer(): Viewer {
  const { auth: state } = useSession();
  if (state.status !== "ready") throw new Error("useViewer must be used inside <AuthGate>");
  return state.viewer;
}
