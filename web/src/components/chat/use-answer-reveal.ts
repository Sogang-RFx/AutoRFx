"use client";
/**
 * Shows the statements of a verified answer one after another: the first ones come out of dots, the rest fade in
 * (plan in `answer-reveal.ts`, run in `answer-run.ts`). The text is always in the page; a waiting statement only has
 * `opacity: 0` and keeps its place. With effects off, with reduced motion, or outside a provider nothing is touched
 * and the answer is just there. Runs once, when the answer first appears.
 *
 * The dots live inside the conversation log only (design §2, rule 2): they start from the rows of dots the waiting
 * state showed, or from the answer's own box, they land on text that is inside the log, and the shared layer is
 * clipped to the log while they fly.
 */
import { useLayoutEffect, type RefObject } from "react";
import { useFx } from "@/components/fx/fx-provider";
import { fxPrefs } from "@/lib/fx/prefs";
import { sampleTarget } from "@/lib/fx/sample";
import { runAnswerReveal, type RevealFrame } from "./answer-run";
import { boxOf, clipInset, intersects, keepInside, startPointFor, type Box } from "./dot-origin";
import { PANEL_SELECTOR, restoreRows, type DraftTrace } from "./draft-trace";

export const STATEMENT_SELECTOR = "[data-statement]";
const TEXT_SELECTOR = "[data-particle-target]";
const LAYER_SELECTOR = "[data-fx-layer]";
const FONT_WAIT_MS = 1200;
/** Dots for one statement; the layer as a whole is capped by the engine. */
const MAX_DOTS_PER_STATEMENT = 500;

function fontsReady(): Promise<void> {
  const ready = document.fonts?.ready;
  if (!ready) return Promise.resolve();
  return Promise.race([ready.then(() => undefined), new Promise<void>((r) => setTimeout(r, FONT_WAIT_MS))]);
}

const nextFrame = (): Promise<void> => new Promise((resolve) => requestAnimationFrame(() => resolve()));

/** What the run needs once the log has scrolled to the answer; null when the dots cannot be kept inside the log. */
function measureFrame(list: HTMLElement, items: readonly HTMLElement[], trace: DraftTrace, turnId: number): RevealFrame | null {
  const panelEl = list.closest(PANEL_SELECTOR);
  const canvas = document.querySelector<HTMLElement>(LAYER_SELECTOR);
  if (!panelEl || !canvas) return null;
  const panel: Box = boxOf(panelEl.getBoundingClientRect());
  const rows = restoreRows(trace.take(turnId), panelEl);
  const answerBox = boxOf((list.parentElement ?? list).getBoundingClientRect());
  const sources = rows.length > 0 ? rows : [answerBox];
  return {
    pointsFor(i) {
      const text = items[i].querySelector(TEXT_SELECTOR);
      if (!text || !intersects(boxOf(text.getBoundingClientRect()), panel)) return null;
      const inside = keepInside(sampleTarget(text, "text", MAX_DOTS_PER_STATEMENT), panel);
      return inside.length > 0 ? inside : null;
    },
    from: (k) => startPointFor(k, sources, panel),
    clip: {
      apply: () => canvas.style.setProperty("clip-path", clipInset(panel, { width: window.innerWidth, height: window.innerHeight })),
      release: () => canvas.style.removeProperty("clip-path"),
    },
  };
}

export function useAnswerReveal(listRef: RefObject<HTMLElement | null>, turnId: number, trace: DraftTrace): void {
  const fx = useFx();
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!fx || !list || !fxPrefs.getState().moves) return;
    const items = [...list.querySelectorAll<HTMLElement>(STATEMENT_SELECTOR)];
    if (items.length === 0) return;
    const run = runAnswerReveal({
      items,
      layer: fx.layer(),
      ready: async () => {
        await fontsReady();
        await nextFrame(); // after the log has scrolled to this answer
        return measureFrame(list, items, trace, turnId);
      },
    });
    // Takes off what this run put on the page and on the shared layer, however the screen goes away.
    return run.cancel;
  }, [fx, listRef, turnId, trace]);
}
