"use client";
import { useEffect, useState } from "react";
import { describeError, isForbidden } from "./provider";

/** `forbidden` marks a 403: the team may not see this, which is a lock, not a failure. */
export type Resource<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "ok"; data: T; error: null }
  | { status: "error"; data: null; error: string; forbidden: boolean };

/**
 * Loads one resource per `key`. When the key changes (for example the team),
 * previous data is dropped immediately so an unmasked response can never stay
 * on screen for a restricted team. `key === null` means "not ready yet".
 */
export function useResource<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>): Resource<T> {
  const [state, setState] = useState<{ key: string | null; res: Resource<T> }>({
    key: null,
    res: { status: "loading", data: null, error: null },
  });

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    // Promise.resolve().then(...) turns a synchronous throw (e.g. a ConfigError
    // for an `http` part without a base URL) into this resource's error state
    // instead of crashing the whole screen (SPEC §13.2, per-part isolation).
    Promise.resolve()
      .then(() => load(controller.signal))
      .then(
      (data) => {
        if (!controller.signal.aborted) setState({ key, res: { status: "ok", data, error: null } });
      },
      (e: unknown) => {
        if (controller.signal.aborted) return;
        setState({ key, res: { status: "error", data: null, error: describeError(e), forbidden: isForbidden(e) } });
      },
    );
    return () => controller.abort();
    // `load` is keyed by `key`: callers encode every input in it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (state.key !== key) return { status: "loading", data: null, error: null };
  return state.res;
}
