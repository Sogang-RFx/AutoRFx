/**
 * When each statement of a verified answer appears. The first ones come out of dots, the rest only fade in, and
 * every one is fully there within `ANSWER_WINDOW_MS` of the answer arriving (docs/design/immersive-ui.md §4.4).
 * With effects off, or reduced motion, everything is shown at once.
 */
import { FLIGHT_JITTER_MS, FLIGHT_MIN_MS, REVEAL_FADE_MS } from "@/lib/fx/motion";

export const STATEMENT_GAP_MS = 120;
export const ANSWER_WINDOW_MS = 1200;
/** Statements that fly in as dots; a flight of the sixth would not end inside the window. */
export const MAX_DOT_STATEMENTS = 5;

export interface StatementPlan {
  delayMs: number;
  withDots: boolean;
}

/** The latest start of a statement that only fades: its fade must still end inside the window. */
const LAST_FADE_START_MS = ANSWER_WINDOW_MS - REVEAL_FADE_MS;

export function planStatements(count: number, moves: boolean): StatementPlan[] {
  if (!moves) return Array.from({ length: count }, () => ({ delayMs: 0, withDots: false }));
  const gap = count > 1 ? Math.min(STATEMENT_GAP_MS, LAST_FADE_START_MS / (count - 1)) : 0;
  return Array.from({ length: count }, (_, i) => ({
    delayMs: Math.round(i * gap),
    withDots: i < MAX_DOT_STATEMENTS,
  }));
}

/** How long a statement that flies in takes from its start until it is shown. */
export const FLIGHT_TOTAL_MS = FLIGHT_MIN_MS + FLIGHT_JITTER_MS;
