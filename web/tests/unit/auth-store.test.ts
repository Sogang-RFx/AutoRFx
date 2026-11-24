/**
 * Session store (docs/auth-rbac.md §6): who is signed in comes from /auth/me only, a dead backend is
 * not "signed out", tabs follow each other, and a changed account or scope has another key.
 * All values here are invented for the test.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createAuthStore, scopeKey, type AuthApi, type AuthChannel, type AuthMessage } from "@/lib/rfq/auth-store";
import type { Viewer } from "@/lib/rfq/contract";
import { ApiError } from "@/lib/rfq/provider";
import { viewer } from "./helpers";

const unauthenticated = () => new ApiError("auth", 401, "unauthenticated", "로그인이 필요합니다.");
const network = () => new ApiError("auth", 0, "network", "서버에 연결하지 못했습니다.");
const ok = (v: Viewer) => ({ data: { viewer: v }, provenance: {} });

function fakeApi(patch: Partial<AuthApi> = {}): AuthApi {
  return {
    me: async () => ok(viewer()),
    login: async () => ok(viewer()),
    logout: async () => ({ data: { ok: true as const }, provenance: {} }),
    changePassword: async () => ok(viewer()),
    startNewSession: () => undefined,
    setAuthListener: () => undefined,
    ...patch,
  };
}

/** Channels joined by a hub, like BroadcastChannel: a message reaches the others, not the sender. */
function hub() {
  const members: Array<{ receive: (m: AuthMessage) => void }> = [];
  const sent: AuthMessage[] = [];
  return {
    sent,
    join(): AuthChannel {
      const me: { receive: (m: AuthMessage) => void } = { receive: () => undefined };
      members.push(me);
      return {
        post: (m) => {
          sent.push(m);
          members.filter((x) => x !== me).forEach((x) => x.receive(m));
        },
        listen: (cb) => {
          me.receive = cb;
          return () => {
            me.receive = () => undefined;
          };
        },
      };
    },
  };
}

const alone = () => hub().join();

describe("the first check", () => {
  it("starts as loading and becomes ready with the viewer", async () => {
    const store = createAuthStore(fakeApi(), alone());
    expect(store.getSnapshot().status).toBe("loading");
    await store.refresh();
    expect(store.getSnapshot()).toMatchObject({ status: "ready", viewer: { user_id: "u-1" } });
  });

  it("is anonymous on 401, without a reason on the first visit", async () => {
    const store = createAuthStore(fakeApi({ me: async () => Promise.reject(unauthenticated()) }), alone());
    await store.refresh();
    expect(store.getSnapshot()).toEqual({ status: "anonymous", reason: null });
  });

  it("says unreachable, not anonymous, when the backend does not answer, and a retry recovers", async () => {
    const me = vi.fn<AuthApi["me"]>().mockRejectedValueOnce(network()).mockResolvedValue(ok(viewer()));
    const store = createAuthStore(fakeApi({ me }), alone());
    await store.refresh();
    expect(store.getSnapshot()).toEqual({ status: "unreachable", error: "서버에 연결하지 못했습니다." });
    await store.refresh();
    expect(store.getSnapshot().status).toBe("ready");
  });

  it("keeps the signed-in screen when a later check cannot reach the backend", async () => {
    const me = vi.fn<AuthApi["me"]>().mockResolvedValueOnce(ok(viewer())).mockRejectedValue(network());
    const store = createAuthStore(fakeApi({ me }), alone());
    await store.refresh();
    await store.refresh();
    expect(store.getSnapshot().status).toBe("ready");
  });

  it("goes anonymous with a reason when a later check gets 401", async () => {
    const me = vi.fn<AuthApi["me"]>().mockResolvedValueOnce(ok(viewer())).mockRejectedValue(unauthenticated());
    const store = createAuthStore(fakeApi({ me }), alone());
    await store.refresh();
    await store.refresh();
    expect(store.getSnapshot()).toEqual({ status: "anonymous", reason: "expired" });
  });
});

describe("login and logout", () => {
  it("is ready after a login that the next check confirms", async () => {
    const startNewSession = vi.fn();
    const store = createAuthStore(fakeApi({ startNewSession }), alone());
    await store.login("quality", "secret-123");
    expect(store.getSnapshot().status).toBe("ready");
    expect(startNewSession).toHaveBeenCalled();
  });

  it("passes a refused login on and stays anonymous", async () => {
    const wrong = new ApiError("auth", 401, "invalid_credentials", "x");
    const store = createAuthStore(
      fakeApi({ me: async () => Promise.reject(unauthenticated()), login: async () => Promise.reject(wrong) }),
      alone(),
    );
    await store.refresh();
    await expect(store.login("quality", "nope")).rejects.toBe(wrong);
    expect(store.getSnapshot()).toEqual({ status: "anonymous", reason: null });
  });

  it("explains a login whose cookie the browser did not keep", async () => {
    const store = createAuthStore(fakeApi({ me: async () => Promise.reject(unauthenticated()) }), alone());
    await store.refresh();
    await expect(store.login("quality", "secret-123")).rejects.toMatchObject({ code: "cookie_blocked" });
    expect(store.getSnapshot().status).toBe("anonymous");
  });

  it("does not let an older check overwrite a login", async () => {
    let fail: (e: unknown) => void = () => undefined;
    const me = vi
      .fn<AuthApi["me"]>()
      .mockImplementationOnce(() => new Promise((_, reject) => (fail = reject)))
      .mockResolvedValue(ok(viewer()));
    const store = createAuthStore(fakeApi({ me }), alone());
    const old = store.refresh();
    await store.login("quality", "secret-123");
    fail(unauthenticated());
    await old;
    expect(store.getSnapshot().status).toBe("ready");
  });

  it("stays signed in when the logout request fails", async () => {
    const store = createAuthStore(fakeApi({ logout: async () => Promise.reject(network()) }), alone());
    await store.refresh();
    await expect(store.logout()).rejects.toThrow("서버에 연결하지 못했습니다.");
    expect(store.getSnapshot().status).toBe("ready");
  });

  it("counts a logout of a session that is already gone as done", async () => {
    const store = createAuthStore(fakeApi({ logout: async () => Promise.reject(unauthenticated()) }), alone());
    await store.refresh();
    await store.logout();
    expect(store.getSnapshot()).toEqual({ status: "anonymous", reason: "signed_out" });
  });

  it("takes the viewer of a password change", async () => {
    const me = async () => ok(viewer({ must_change_password: true }));
    const store = createAuthStore(fakeApi({ me }), alone());
    await store.refresh();
    await store.changePassword("old", "new-secret-1");
    expect(store.getSnapshot()).toMatchObject({ status: "ready", viewer: { must_change_password: false } });
  });
});

describe("the 401 hook", () => {
  function hooked(me: AuthApi["me"]) {
    let heard: (e: ApiError) => void = () => undefined;
    const store = createAuthStore(fakeApi({ me, setAuthListener: (cb) => (heard = cb ?? heard) }), alone());
    const stop = store.start();
    return { store, stop, hear: (e: ApiError) => heard(e) };
  }

  it("clears the screen at once", async () => {
    const { store, stop, hear } = hooked(async () => ok(viewer()));
    await vi.waitFor(() => expect(store.getSnapshot().status).toBe("ready"));
    hear(unauthenticated());
    expect(store.getSnapshot()).toEqual({ status: "anonymous", reason: "expired" });
    stop();
  });

  it("asks again who is signed in when the password has to be changed", async () => {
    const me = vi
      .fn<AuthApi["me"]>()
      .mockResolvedValueOnce(ok(viewer()))
      .mockResolvedValue(ok(viewer({ must_change_password: true })));
    const { store, stop, hear } = hooked(me);
    await vi.waitFor(() => expect(store.getSnapshot().status).toBe("ready"));
    hear(new ApiError("document", 403, "password_change_required", "x"));
    await vi.waitFor(() => expect(store.getSnapshot()).toMatchObject({ viewer: { must_change_password: true } }));
    stop();
  });
});

describe("other tabs", () => {
  let tabs: ReturnType<typeof hub>;
  beforeEach(() => {
    tabs = hub();
  });
  afterEach(() => vi.restoreAllMocks());

  async function pair(meB: AuthApi["me"]) {
    const a = createAuthStore(fakeApi(), tabs.join());
    const b = createAuthStore(fakeApi({ me: meB }), tabs.join());
    const stops = [a.start(), b.start()];
    await vi.waitFor(() => expect([a, b].map((s) => s.getSnapshot().status)).toEqual(["ready", "ready"]));
    return { a, b, stop: () => stops.forEach((s) => s()) };
  }

  it("clears tab B at once when tab A signs out", async () => {
    const { a, b, stop } = await pair(async () => ok(viewer()));
    await a.logout();
    expect(b.getSnapshot()).toEqual({ status: "anonymous", reason: "signed_out" });
    stop();
  });

  it("hides tab B until it knows the account that tab A signed in with", async () => {
    let current = viewer();
    const { b, stop } = await pair(async () => ok(current));
    current = viewer({ user_id: "u-9", username: "legal" });
    const states: string[] = [];
    b.subscribe(() => states.push(b.getSnapshot().status));
    // A third tab of the same browser signs in as another account.
    const c = createAuthStore(
      fakeApi({ login: async () => ok(current), me: async () => ok(current) }),
      tabs.join(),
    );
    await c.login("legal", "secret-123");
    await vi.waitFor(() => expect(b.getSnapshot()).toMatchObject({ viewer: { user_id: "u-9" } }));
    expect(states[0]).toBe("loading");
    expect(JSON.stringify(tabs.sent)).not.toMatch(/secret|token|legal/);
    stop();
  });
});

describe("what makes screen state another account's", () => {
  it("changes with the account and with its scope, not with its name", () => {
    const base = scopeKey(viewer());
    expect(scopeKey(viewer({ display_name: "다른 이름" }))).toBe(base);
    expect(scopeKey(viewer({ user_id: "u-2" }))).not.toBe(base);
    expect(scopeKey(viewer({ department: "legal" }))).not.toBe(base);
    expect(scopeKey(viewer({ group: "cost_visible" }))).not.toBe(base);
    expect(scopeKey(viewer({ full_scope: true }))).not.toBe(base);
  });
});
