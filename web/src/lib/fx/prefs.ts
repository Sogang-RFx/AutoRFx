/**
 * Effects switch and the "does anything move" answer (docs/design/immersive-ui.md §2 rule 4).
 *
 * Sources, in order: `?fx=0` (this page load only), the stored switch (`localStorage`, default on) and the live
 * `prefers-reduced-motion`. Safe when storage throws and during SSR (nothing moves there).
 */

export const FX_STORAGE_KEY = "autorfx.fx";
export const FX_QUERY_PARAM = "fx";
const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface FxEnv {
  isBrowser: boolean;
  /** `null` when storage is missing or blocked. May also throw: that is treated as blocked. */
  storage(): StorageLike | null;
  /** The page's query string, e.g. "?fx=0". */
  search(): string;
  reducedMotion(): boolean;
  /** Calls `onChange` when the stored switch or the reduced-motion setting changes; returns the detach function. */
  watch(onChange: () => void): () => void;
}

export interface FxState {
  /** The switch, after `?fx=0` has been applied. */
  enabled: boolean;
  /** Turned off by `?fx=0` for this page load: the switch cannot turn it on. */
  locked: boolean;
  /** The system asks for reduced motion. */
  reduced: boolean;
  /** `enabled && !reduced` in a browser: the single answer for "may anything move". */
  moves: boolean;
}

export interface FxPrefs {
  /** Same object until something changes, so it works as a `useSyncExternalStore` snapshot. */
  getState(): FxState;
  subscribe(listener: () => void): () => void;
  setEnabled(on: boolean): void;
}

/** What the server renders and what hydration starts from: nothing moves. */
export const SERVER_FX_STATE: FxState = Object.freeze({ enabled: true, locked: false, reduced: false, moves: false });

function sameState(a: FxState | null, b: FxState): boolean {
  return a !== null && a.enabled === b.enabled && a.locked === b.locked && a.reduced === b.reduced && a.moves === b.moves;
}

function safely<T>(read: () => T, fallback: T): T {
  try {
    return read();
  } catch {
    return fallback;
  }
}

export function createFxPrefs(env: FxEnv): FxPrefs {
  const listeners = new Set<() => void>();
  let detach: (() => void) | null = null;
  let state: FxState | null = null;
  // Remembers the choice when storage is blocked, so the switch still works for this page.
  let memory: boolean | null = null;
  // `?fx=0` belongs to the page load: a client-side navigation must not forget it.
  let locked: boolean | null = null;

  function compute(): FxState {
    if (!env.isBrowser) return SERVER_FX_STATE;
    if (locked === null) locked = safely(() => new URLSearchParams(env.search()).get(FX_QUERY_PARAM) === "0", false);
    const stored = memory ?? safely(() => env.storage()?.getItem(FX_STORAGE_KEY) !== "0", true);
    const reduced = safely(() => env.reducedMotion(), false);
    const enabled = stored && !locked;
    const next: FxState = { enabled, locked, reduced, moves: enabled && !reduced };
    return sameState(state, next) ? (state as FxState) : next;
  }

  function refresh(): void {
    const next = compute();
    if (next === state) return;
    state = next;
    listeners.forEach((listener) => listener());
  }

  function onExternalChange(): void {
    // Another tab wrote the switch: forget what this page remembered and read storage again.
    memory = null;
    refresh();
  }

  return {
    getState() {
      // While someone listens, the cache is kept fresh by `watch`; otherwise read again.
      if (state === null || listeners.size === 0) state = compute();
      return state;
    },
    subscribe(listener) {
      listeners.add(listener);
      if (listeners.size === 1 && env.isBrowser) {
        state = compute();
        detach = env.watch(onExternalChange);
      }
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0 && detach) {
          detach();
          detach = null;
        }
      };
    },
    setEnabled(on) {
      memory = on;
      safely(() => env.storage()?.setItem(FX_STORAGE_KEY, on ? "1" : "0"), undefined);
      refresh();
    },
  };
}

function createBrowserEnv(): FxEnv {
  const isBrowser = typeof window !== "undefined";
  let query: MediaQueryList | null = null;
  const media = (): MediaQueryList | null => {
    if (!isBrowser || typeof window.matchMedia !== "function") return null;
    query ??= window.matchMedia(REDUCED_MOTION_QUERY);
    return query;
  };
  return {
    isBrowser,
    storage: () => (isBrowser ? window.localStorage : null),
    search: () => (isBrowser ? window.location.search : ""),
    reducedMotion: () => media()?.matches ?? false,
    watch(onChange) {
      const list = media();
      const onStorage = (e: StorageEvent) => {
        if (e.key === null || e.key === FX_STORAGE_KEY) onChange();
      };
      list?.addEventListener("change", onChange);
      window.addEventListener("storage", onStorage);
      return () => {
        list?.removeEventListener("change", onChange);
        window.removeEventListener("storage", onStorage);
      };
    },
  };
}

export const fxPrefs: FxPrefs = createFxPrefs(createBrowserEnv());

export const getFxState = (): FxState => fxPrefs.getState();
export const subscribeFx = (listener: () => void): (() => void) => fxPrefs.subscribe(listener);
export const setFxEnabled = (on: boolean): void => fxPrefs.setEnabled(on);
/** True only when effects are on, the system allows motion and we are in a browser. */
export const fxMoves = (): boolean => fxPrefs.getState().moves;

// ── Intro replay channel ─────────────────────────────────────────────────────────────────────────────────────────

export interface ReplayChannel {
  request(): void;
  on(listener: () => void): () => void;
}

export function createReplayChannel(): ReplayChannel {
  const listeners = new Set<() => void>();
  return {
    request() {
      [...listeners].forEach((listener) => listener());
    },
    on(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

const introReplay = createReplayChannel();

/** Asks the intro to play again (account menu). Does nothing when the intro is not mounted. */
export const requestIntroReplay = (): void => introReplay.request();
/** The intro subscribes here. Returns the unsubscribe function. */
export const onIntroReplay = (listener: () => void): (() => void) => introReplay.on(listener);
