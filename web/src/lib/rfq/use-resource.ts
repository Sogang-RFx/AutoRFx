"use client";
import { useEffect, useState } from "react";
import { scopeKey } from "./auth-store";
import { describeError, isForbidden } from "./provider";
import { useSession } from "./session";

/** `forbidden` marks a 403: the account may not see this, which is a lock, not a failure. */
export type Resource<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "ok"; data: T; error: null }
  | { status: "error"; data: null; error: string; forbidden: boolean };

/**
 * Loads one resource per account and `key`. When either changes, previous data is dropped
 * immediately so nothing one account may see can stay on screen for another. `key === null` means
 * "not ready yet". A new `version` loads the same resource again and keeps the old data meanwhile.
 */
export function useResource<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  version = 0,
): Resource<T> {
  const { auth } = useSession();
  const owner = auth.status === "ready" ? scopeKey(auth.viewer) : null;
  const fullKey = key === null || owner === null ? null : `${owner} ${key}`;
  const [state, setState] = useState<{ key: string | null; res: Resource<T> }>({
    key: null,
    res: { status: "loading", data: null, error: null },
  });

  useEffect(() => {
    if (fullKey === null) return;
    const controller = new AbortController();
    // Promise.resolve().then(...) turns a synchronous throw (e.g. a ConfigError
    // without a base URL) into this resource's error state instead of crashing
    // the whole screen (SPEC §13.2, per-part isolation).
    Promise.resolve()
      .then(() => load(controller.signal))
      .then(
        (data) => {
          if (!controller.signal.aborted) setState({ key: fullKey, res: { status: "ok", data, error: null } });
        },
        (e: unknown) => {
          if (controller.signal.aborted) return;
          setState({
            key: fullKey,
            res: { status: "error", data: null, error: describeError(e), forbidden: isForbidden(e) },
          });
        },
      );
    return () => controller.abort();
    // `load` is keyed by `key`: callers encode every input in it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fullKey, version]);

  if (fullKey === null || state.key !== fullKey) return { status: "loading", data: null, error: null };
  return state.res;
}
