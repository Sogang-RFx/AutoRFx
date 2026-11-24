import { ApiError } from "./provider";

/** How often to ask again while the analysis runs; the processing screen polls the job at the same pace. */
export const READY_POLL_MS = 1500;

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason);
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(signal.reason);
      },
      { once: true },
    );
  });
}

/**
 * Runs `load` until the backend stops answering 409 `not_ready` (SPEC §14). Results of an
 * RFQ whose analysis is still running are not served yet, so a screen opened right after
 * upload asks again instead of showing an error. Any other error, and leaving the screen
 * (abort), ends the wait.
 */
export async function untilReady<T>(
  load: () => Promise<T>,
  signal: AbortSignal,
  intervalMs: number = READY_POLL_MS,
): Promise<T> {
  for (;;) {
    try {
      return await load();
    } catch (e) {
      if (!(e instanceof ApiError && e.code === "not_ready") || signal.aborted) throw e;
    }
    await wait(intervalMs, signal);
  }
}
