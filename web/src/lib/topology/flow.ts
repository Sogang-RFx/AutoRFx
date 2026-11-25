/**
 * The flow animation: small dots travel once along the edges, from the clause to its department.
 * Pure timing and positions; the scene only asks "where is dot k at time t".
 */
export const FLOW_TOTAL_MS = 1500;
export const FLOW_TRAVEL_MS = 760;
/** Enough to read as a flow, few enough to stay cheap and quiet on a map of thousands. */
export const MAX_FLOW_DOTS = 360;

/** At most `max` edge ids spread evenly over `ids`, in the same order. */
export function sampleEdges(ids: readonly number[], max = MAX_FLOW_DOTS): number[] {
  if (ids.length <= max) return [...ids];
  const out: number[] = [];
  for (let k = 0; k < max; k += 1) out.push(ids[Math.floor((k * ids.length) / max)]);
  return out;
}

/** Ease-out so a dot leaves quickly and settles on the department. */
const easeOut = (t: number): number => 1 - (1 - t) * (1 - t);

/**
 * Progress 0..1 of dot k of n at `elapsedMs`, or null while it has not started or after it arrived.
 * Starts are spread evenly so the last dot arrives at FLOW_TOTAL_MS.
 */
export function dotProgress(k: number, n: number, elapsedMs: number): number | null {
  if (n <= 0) return null;
  const start = n === 1 ? 0 : (k / (n - 1)) * (FLOW_TOTAL_MS - FLOW_TRAVEL_MS);
  const t = (elapsedMs - start) / FLOW_TRAVEL_MS;
  if (t < 0 || t >= 1) return null;
  return easeOut(t);
}

export const isFlowDone = (elapsedMs: number): boolean => elapsedMs >= FLOW_TOTAL_MS;

/** Writes x, y of every moving dot into `out` (two numbers each) and returns how many are on the way. */
export function dotPositions(edges: Float32Array, ids: readonly number[], elapsedMs: number, out: Float32Array): number {
  let shown = 0;
  ids.forEach((edge, k) => {
    const p = dotProgress(k, ids.length, elapsedMs);
    if (p === null) return;
    const o = edge * 4;
    out[shown * 2] = edges[o] + (edges[o + 2] - edges[o]) * p;
    out[shown * 2 + 1] = edges[o + 1] + (edges[o + 3] - edges[o + 1]) * p;
    shown += 1;
  });
  return shown;
}

/** Back from the list the scene is new, but the flow already played: keep the token, drop the department. */
export function flowOnReturn(flow: { token: number; department: string | null }): { token: number; department: null } {
  return { token: Math.max(flow.token, 1), department: null };
}
