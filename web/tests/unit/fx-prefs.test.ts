/** Effects switch: storage that throws, ?fx=0, live reduced motion, SSR and the replay channel. */
import { describe, expect, it, vi } from "vitest";
import { createFxPrefs, createReplayChannel, FX_STORAGE_KEY, SERVER_FX_STATE, type FxEnv, type StorageLike } from "@/lib/fx/prefs";

type FakeOptions = Partial<FxEnv> & { stored?: string | null; query?: string; reduced?: boolean };

function fakeEnv(options: FakeOptions = {}) {
  const { stored, query, reduced, ...overrides } = options;
  const data = new Map<string, string>();
  if (stored != null) data.set(FX_STORAGE_KEY, stored);
  const state = { reduced: reduced ?? false };
  let notify: () => void = () => undefined;
  const storage: StorageLike = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
  const env: FxEnv = {
    isBrowser: true,
    storage: () => storage,
    search: () => query ?? "",
    reducedMotion: () => state.reduced,
    watch: (onChange) => {
      notify = onChange;
      return () => {
        notify = () => undefined;
      };
    },
    ...overrides,
  };
  return { env, data, setReduced: (v: boolean) => ((state.reduced = v), notify()), fireStorage: () => notify() };
}

const throwing = (): StorageLike => ({
  getItem: () => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
});

describe("effects switch", () => {
  it("is on by default and moves", () => {
    const { env } = fakeEnv();
    expect(createFxPrefs(env).getState()).toEqual({ enabled: true, locked: false, reduced: false, moves: true });
  });

  it("remembers the choice in storage", () => {
    const { env, data } = fakeEnv();
    const prefs = createFxPrefs(env);
    prefs.setEnabled(false);
    expect(data.get(FX_STORAGE_KEY)).toBe("0");
    expect(prefs.getState().moves).toBe(false);
    expect(createFxPrefs(env).getState().enabled).toBe(false);
    prefs.setEnabled(true);
    expect(data.get(FX_STORAGE_KEY)).toBe("1");
  });

  it("works when storage throws: default on, and the switch still works for this page", () => {
    const { env } = fakeEnv({ storage: throwing });
    const prefs = createFxPrefs(env);
    expect(prefs.getState().enabled).toBe(true);
    expect(() => prefs.setEnabled(false)).not.toThrow();
    expect(prefs.getState().enabled).toBe(false);
    prefs.setEnabled(true);
    expect(prefs.getState().enabled).toBe(true);
  });

  it("works when storage is missing", () => {
    const { env } = fakeEnv({ storage: () => null });
    const prefs = createFxPrefs(env);
    prefs.setEnabled(false);
    expect(prefs.getState().enabled).toBe(false);
  });

  it("?fx=0 turns it off for the page and the switch cannot override it", () => {
    const { env, data } = fakeEnv({ query: "?fx=0&x=1" });
    const prefs = createFxPrefs(env);
    expect(prefs.getState()).toMatchObject({ enabled: false, locked: true, moves: false });
    prefs.setEnabled(true);
    expect(prefs.getState().moves).toBe(false);
    expect(data.get(FX_STORAGE_KEY)).toBe("1");
  });

  it("?fx=0 is remembered after the address changes", () => {
    let search = "?fx=0";
    const { env } = fakeEnv();
    const prefs = createFxPrefs({ ...env, search: () => search });
    expect(prefs.getState().locked).toBe(true);
    search = "";
    expect(prefs.getState().locked).toBe(true);
  });

  it("any other fx value does not lock", () => {
    expect(createFxPrefs(fakeEnv({ query: "?fx=1" }).env).getState().locked).toBe(false);
  });

  it("reduced motion stops everything moving but leaves the switch alone", () => {
    const state = createFxPrefs(fakeEnv({ reduced: true }).env).getState();
    expect(state).toMatchObject({ enabled: true, reduced: true, moves: false });
  });

  it("follows reduced motion live while someone listens", () => {
    const { env, setReduced } = fakeEnv();
    const prefs = createFxPrefs(env);
    const listener = vi.fn();
    const stop = prefs.subscribe(listener);
    expect(prefs.getState().moves).toBe(true);
    setReduced(true);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(prefs.getState().moves).toBe(false);
    setReduced(false);
    expect(prefs.getState().moves).toBe(true);
    stop();
  });

  it("follows another tab's change to storage", () => {
    const { env, data, fireStorage } = fakeEnv();
    const prefs = createFxPrefs(env);
    prefs.subscribe(() => undefined);
    prefs.setEnabled(true);
    data.set(FX_STORAGE_KEY, "0");
    fireStorage();
    expect(prefs.getState().enabled).toBe(false);
  });

  it("returns the same object until something changes (a safe snapshot)", () => {
    const { env } = fakeEnv();
    const prefs = createFxPrefs(env);
    prefs.subscribe(() => undefined);
    expect(prefs.getState()).toBe(prefs.getState());
    const before = prefs.getState();
    prefs.setEnabled(true);
    expect(prefs.getState()).toBe(before);
  });

  it("stops watching when the last listener leaves", () => {
    const detach = vi.fn();
    const { env } = fakeEnv({ watch: () => detach });
    const prefs = createFxPrefs(env);
    const a = prefs.subscribe(() => undefined);
    const b = prefs.subscribe(() => undefined);
    a();
    expect(detach).not.toHaveBeenCalled();
    b();
    expect(detach).toHaveBeenCalledTimes(1);
  });

  it("without a browser nothing moves and nothing is touched", () => {
    const touched = vi.fn();
    const env: FxEnv = { isBrowser: false, storage: touched, search: touched, reducedMotion: touched, watch: touched };
    const prefs = createFxPrefs(env);
    expect(prefs.getState()).toBe(SERVER_FX_STATE);
    expect(SERVER_FX_STATE.moves).toBe(false);
    prefs.subscribe(() => undefined)();
    expect(touched).not.toHaveBeenCalled();
  });

  it("survives a media query that throws", () => {
    const { env } = fakeEnv({
      reducedMotion: () => {
        throw new Error("no matchMedia");
      },
    });
    expect(createFxPrefs(env).getState().moves).toBe(true);
  });
});

describe("intro replay channel", () => {
  it("reaches every listener until it unsubscribes", () => {
    const channel = createReplayChannel();
    const a = vi.fn();
    const b = vi.fn();
    const offA = channel.on(a);
    channel.on(b);
    channel.request();
    offA();
    channel.request();
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(2);
  });

  it("does nothing without listeners", () => {
    expect(() => createReplayChannel().request()).not.toThrow();
  });
});
