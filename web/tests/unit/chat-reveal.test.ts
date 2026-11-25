/** When each verified statement of an answer appears (docs/design/immersive-ui.md §4.4). */
import { describe, expect, it } from "vitest";
import {
  ANSWER_WINDOW_MS,
  MAX_DOT_STATEMENTS,
  STATEMENT_GAP_MS,
  planStatements,
} from "@/components/chat/answer-reveal";
import { FLIGHT_JITTER_MS, FLIGHT_MIN_MS, REVEAL_FADE_MS } from "@/lib/fx/motion";

describe("planStatements", () => {
  it("starts each statement 120 ms after the one before", () => {
    expect(planStatements(4, true).map((p) => p.delayMs)).toEqual([0, 120, 240, 360]);
    expect(STATEMENT_GAP_MS).toBe(120);
  });

  it("gives a single statement no delay", () => {
    expect(planStatements(1, true)).toEqual([{ delayMs: 0, withDots: true }]);
    expect(planStatements(0, true)).toEqual([]);
  });

  it("lets the first few come out of dots and the later ones only fade", () => {
    const plan = planStatements(8, true);
    expect(plan.map((p) => p.withDots)).toEqual(plan.map((_, i) => i < MAX_DOT_STATEMENTS));
  });

  it("is over, for every statement, within 1.2 seconds, however many there are", () => {
    for (const count of [1, 2, 5, 8, 12, 30, 100]) {
      for (const { delayMs, withDots } of planStatements(count, true)) {
        const takes = withDots ? FLIGHT_MIN_MS + FLIGHT_JITTER_MS : REVEAL_FADE_MS;
        expect(delayMs + takes, `${count} statements`).toBeLessThanOrEqual(ANSWER_WINDOW_MS);
      }
    }
  });

  it("keeps the order, and squeezes the gap only when 120 ms would not fit", () => {
    const delays = planStatements(30, true).map((p) => p.delayMs);
    expect([...delays].sort((a, b) => a - b)).toEqual(delays);
    expect(delays[1] - delays[0]).toBeLessThan(STATEMENT_GAP_MS);
    expect(planStatements(5, true)[1].delayMs).toBe(STATEMENT_GAP_MS);
  });

  it("shows everything at once, without dots, when nothing moves", () => {
    expect(planStatements(3, false)).toEqual(Array(3).fill({ delayMs: 0, withDots: false }));
  });
});
