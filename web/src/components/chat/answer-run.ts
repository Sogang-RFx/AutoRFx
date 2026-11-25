/**
 * One run of the answer reveal (plan in `answer-reveal.ts`), without React or the DOM so it can be tested: the
 * statements wait, the first ones come out of dots on the shared layer, and the run ends with plain text. The run
 * puts dots on the layer only after it has seen the layer empty, and `cancel` takes off exactly what the run put
 * there (the dots and the clip), in every path.
 */
import { FLIGHT_JITTER_MS, FLIGHT_MIN_MS, HANDOFF_AT, REVEAL_FADE_MS } from "@/lib/fx/motion";
import type { ParticleLayer } from "@/lib/fx/particles";
import { TONE, type Tone } from "@/lib/fx/tone";
import { createStatementGate, type Attributed } from "./answer-gate";
import { FLIGHT_TOTAL_MS, planStatements } from "./answer-reveal";

export type RevealLayer = Pick<ParticleLayer, "dotCount" | "assemble" | "fadeTo" | "clear">;

/** What the run needs from the page once the fonts are ready and the log has scrolled to the answer. */
export interface RevealFrame {
  /** Where the dots of statement `i` land; null when none of it is inside the panel. Only points inside the panel. */
  pointsFor(i: number): Float32Array | null;
  /** Where the k-th dot starts: inside the panel. */
  from(k: number): readonly [number, number];
  /** Cuts the shared layer down to the panel while the dots fly, and gives it back. */
  clip: { apply(): void; release(): void };
}

export interface RunOptions {
  items: readonly Attributed[];
  layer: RevealLayer | null;
  /** Resolves when fonts and layout are settled; null for "no dots". */
  ready: () => Promise<RevealFrame | null>;
}

/** Whatever happens, a waiting statement is shown after this long. */
export const SAFETY_MS = 4800;

export function runAnswerReveal({ items, layer, ready }: RunOptions): { cancel(): void } {
  const plan = planStatements(items.length, true);
  const gate = createStatementGate(items, SAFETY_MS);
  const timers = new Set<ReturnType<typeof setTimeout>>();
  let isCancelled = false;
  let flight: { layer: RevealLayer; frame: RevealFrame } | null = null;

  const later = (fn: () => void, ms: number) => {
    const id = setTimeout(() => {
      timers.delete(id);
      if (!isCancelled) fn();
    }, ms);
    timers.add(id);
  };

  function endFlight(): void {
    if (!flight) return;
    const { layer: flown, frame } = flight;
    flight = null;
    flown.clear();
    frame.clip.release();
  }

  async function play(): Promise<void> {
    const frame = await ready();
    if (isCancelled) return;
    // A layer that already holds dots belongs to something else: the statements only fade.
    const fly = frame && layer && layer.dotCount === 0 ? { layer, frame } : null;
    const groups: { points: Float32Array; delay: number; tone: Tone }[] = [];
    items.forEach((el, i) => {
      const { delayMs, withDots } = plan[i];
      const points = fly && withDots ? fly.frame.pointsFor(i) : null;
      if (points && points.length > 0) {
        groups.push({ points, delay: delayMs, tone: TONE.ink });
        later(() => gate.show(el), delayMs + FLIGHT_TOTAL_MS * HANDOFF_AT);
      } else {
        later(() => gate.show(el), delayMs);
      }
    });
    if (fly && groups.length > 0) {
      flight = fly;
      fly.frame.clip.apply();
      void fly.layer.fadeTo(1, 1);
      await fly.layer.assemble(groups, { duration: FLIGHT_MIN_MS, jitter: FLIGHT_JITTER_MS, from: fly.frame.from });
      if (isCancelled) return;
      await fly.layer.fadeTo(0, REVEAL_FADE_MS);
      if (isCancelled) return;
      endFlight();
    }
    later(() => gate.releaseAll(), REVEAL_FADE_MS + FLIGHT_TOTAL_MS);
  }

  function cancel(): void {
    isCancelled = true;
    timers.forEach(clearTimeout);
    timers.clear();
    endFlight();
    gate.releaseAll();
  }

  play().catch(() => {
    // The reveal is decoration: whatever broke, the answer is shown and the layer is given back.
    endFlight();
    gate.releaseAll();
  });

  return { cancel };
}
