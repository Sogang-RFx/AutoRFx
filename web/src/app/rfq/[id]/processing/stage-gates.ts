/**
 * Job stages as gates of a dot flow (docs/design/immersive-ui.md §4.3). Pure: the only input is the job response the
 * page already polls, so the dots can show nothing the server did not say.
 */
import type { FlowGate, FlowLane, GateState } from "@/lib/fx/flow";
import type { StageStatus } from "@/lib/rfq/contract";

/** Gap between the bottom of a round icon and the dots resting under it, on the connector line. */
export const RAIL_REST_GAP_PX = 6;

/**
 * Has this page seen a stage running? Dots appear only while something is in transit or work is awaited (design §2
 * rule 1), so a job opened already finished stays still. Once seen, it stays seen: the dots settle with the job.
 */
export function hasSeenRunning(seen: boolean, statuses: readonly StageStatus[]): boolean {
  return seen || statuses.includes("running");
}

const GATE_STATE: Record<StageStatus, GateState> = {
  pending: "pending",
  running: "active",
  done: "done",
  error: "error",
  skipped: "skipped",
};

export function gateState(status: StageStatus): GateState {
  return GATE_STATE[status];
}

/** y of each gate in the canvas's own px: just under each round icon, on the connector (icon bottoms in the viewport). */
export function railYs(iconBottoms: readonly number[], canvasTop: number): number[] {
  return iconBottoms.map((bottom) => bottom - canvasTop + RAIL_REST_GAP_PX);
}

/**
 * One lane, one gate per stage, down the rail at `x`. When the measured rows do not match the stages (the list is
 * not laid out yet) the result is empty and no dot is drawn.
 */
export function stageLanes(statuses: readonly StageStatus[], x: number, ys: readonly number[]): FlowLane[] {
  if (!statuses.length || statuses.length !== ys.length) return [];
  const gates: FlowGate[] = statuses.map((status, i) => ({ x, y: ys[i], state: gateState(status) }));
  return [{ gates }];
}
