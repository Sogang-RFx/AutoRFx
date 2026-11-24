/**
 * Who is signed in, kept outside React so it can be tested without a browser.
 *
 * The only source is the backend's `viewer` (docs/auth-rbac.md §1): nothing about the identity is
 * stored in the browser. A backend that does not answer is `unreachable`, never `anonymous`.
 * Tabs of one browser share the cookie, so they tell each other when it changes.
 */
import type { OkResponse, Viewer, ViewerResponse } from "./contract";
import { ApiError, describeError } from "./provider";

export type AnonymousReason = null | "expired" | "signed_out";

export type AuthState =
  | { status: "loading" }
  | { status: "anonymous"; reason: AnonymousReason }
  | { status: "unreachable"; error: string }
  | { status: "ready"; viewer: Viewer };

/** What tabs tell each other. The user id only says "somebody else": the receiver asks the server. */
export type AuthMessage = { type: "signed_out" } | { type: "signed_in"; user_id: string } | { type: "check" };

export type AuthChannel = {
  post: (message: AuthMessage) => void;
  /** Returns a function that stops listening. */
  listen: (receive: (message: AuthMessage) => void) => () => void;
};

export type AuthApi = {
  me: () => Promise<ViewerResponse>;
  login: (body: { username: string; password: string }) => Promise<ViewerResponse>;
  logout: () => Promise<OkResponse>;
  changePassword: (body: { current_password: string; new_password: string }) => Promise<ViewerResponse>;
  startNewSession: () => void;
  setAuthListener: (listener: ((e: ApiError) => void) | null) => void;
};

export type AuthStore = {
  getSnapshot: () => AuthState;
  subscribe: (listener: () => void) => () => void;
  /** Begins to listen (401 hook, other tabs) and asks who is signed in. Returns a function that stops. */
  start: () => () => void;
  /** Asks the server who is signed in. `hide` drops the screen until the answer is here. */
  refresh: (hide?: boolean) => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (current: string, next: string) => Promise<void>;
};

/** Screen state belongs to one account with one scope; a change of either discards all of it. */
export function scopeKey(v: Viewer): string {
  return JSON.stringify([v.user_id, v.role, v.department, v.group, v.full_scope]);
}

const sameViewer = (a: Viewer, b: Viewer) =>
  (Object.keys(b) as (keyof Viewer)[]).every((k) => a[k] === b[k]);

const isUnauthenticated = (e: unknown) => e instanceof ApiError && e.status === 401;

export function createAuthStore(api: AuthApi, channel: AuthChannel): AuthStore {
  let state: AuthState = { status: "loading" };
  const listeners = new Set<() => void>();
  /** Raised whenever the answer of an earlier /auth/me would be out of date. */
  let version = 0;

  function set(next: AuthState): void {
    if (state.status === "ready" && next.status === "ready" && sameViewer(state.viewer, next.viewer)) return;
    if (next.status === "ready" && (state.status !== "ready" || state.viewer.user_id !== next.viewer.user_id)) {
      api.startNewSession();
    }
    state = next;
    listeners.forEach((l) => l());
  }

  /** Signed out, with a reason only for someone who was signed in. */
  function lose(reason: AnonymousReason): void {
    version += 1;
    if (state.status === "anonymous") return;
    set({ status: "anonymous", reason: state.status === "ready" || reason === "signed_out" ? reason : null });
  }

  async function refresh(hide = false): Promise<void> {
    const mine = ++version;
    const before = state;
    if (hide) set({ status: "loading" });
    try {
      const res = await api.me();
      if (mine === version) set({ status: "ready", viewer: res.data.viewer });
    } catch (e) {
      if (mine !== version) return;
      if (isUnauthenticated(e)) {
        set(before.status === "anonymous" ? before : { status: "anonymous", reason: before.status === "ready" ? "expired" : null });
      } else if (state.status !== "ready") {
        // Screens that are open keep their data and report their own failures.
        set({ status: "unreachable", error: describeError(e) });
      }
    }
  }

  function onMessage(message: AuthMessage): void {
    if (message.type === "signed_out") return lose("signed_out");
    const isOther = message.type === "signed_in" && state.status === "ready" && state.viewer.user_id !== message.user_id;
    void refresh(isOther);
  }

  function onAuthFailure(e: ApiError): void {
    if (e.status === 401) {
      if (state.status !== "ready") return;
      lose("expired");
      channel.post({ type: "check" });
    } else {
      void refresh();
    }
  }

  return {
    getSnapshot: () => state,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    start: () => {
      api.setAuthListener(onAuthFailure);
      const stopListening = channel.listen(onMessage);
      void refresh();
      return () => {
        api.setAuthListener(null);
        stopListening();
      };
    },
    refresh,
    login: async (username, password) => {
      await api.login({ username, password });
      version += 1;
      let confirmed: ViewerResponse;
      try {
        // The answer alone does not prove that the browser kept the cookie.
        confirmed = await api.me();
      } catch (e) {
        if (!isUnauthenticated(e)) throw e;
        throw new ApiError("auth", 401, "cookie_blocked", "로그인 쿠키가 저장되지 않았습니다.");
      }
      version += 1;
      set({ status: "ready", viewer: confirmed.data.viewer });
      channel.post({ type: "signed_in", user_id: confirmed.data.viewer.user_id });
    },
    logout: async () => {
      try {
        await api.logout();
      } catch (e) {
        if (!isUnauthenticated(e)) throw e;
      }
      lose("signed_out");
      channel.post({ type: "signed_out" });
    },
    changePassword: async (current, next) => {
      const res = await api.changePassword({ current_password: current, new_password: next });
      version += 1;
      // The old session ended: a 401 to one of its requests says nothing about the new one.
      api.startNewSession();
      set({ status: "ready", viewer: res.data.viewer });
      channel.post({ type: "check" });
    },
  };
}
