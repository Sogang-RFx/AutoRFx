/**
 * Browser-only helpers of the intro: the fonts to sample, waiting until the screen underneath has settled, and
 * choosing the elements the dots fly to. The choreography itself is in intro-player.ts.
 */
import { collectTargets, FX_ATTRIBUTE } from "@/lib/fx/reveal";
import { TONE, type Tone } from "@/lib/fx/tone";
import { INTRO_TIMING, TAGLINE, WORDMARK } from "./intro-plan";

const TONE_ATTRIBUTE = "data-particle-tone";
const SCREEN_LANDMARKS = "main, h1";

export interface Families {
  serif: string;
  sans: string;
}

/** The font stacks the page really uses (next/font names), read from probe elements: canvas cannot read `var()`. */
export function probeFamilies(): Families {
  const read = (className: string): string => {
    const probe = document.createElement("span");
    probe.className = className;
    probe.textContent = "A";
    probe.style.cssText = "position:absolute;visibility:hidden;pointer-events:none";
    document.body.appendChild(probe);
    const family = getComputedStyle(probe).fontFamily;
    probe.remove();
    return family;
  };
  return { serif: read("font-serif"), sans: read("") };
}

/**
 * Asks the browser for the two faces the name is drawn in (a blank page has not used them yet, so `fonts.ready`
 * alone would resolve with a fallback), then waits for the font set. Gives up quietly after `fontWaitMs`.
 */
export async function prepareFonts(wordFont: string, tagFont: string): Promise<void> {
  const set = document.fonts;
  if (!set) return;
  const loading = Promise.all([set.load(wordFont, WORDMARK), set.load(tagFont, TAGLINE)])
    .then(() => set.ready)
    .then(() => undefined)
    .catch(() => undefined);
  await Promise.race([loading, new Promise<void>((resolve) => setTimeout(resolve, INTRO_TIMING.fontWaitMs))]);
}

/** The page's own fonts, so the real elements are sampled with the glyphs they will show. */
export async function fontsReady(): Promise<void> {
  const ready = document.fonts?.ready;
  if (!ready) return;
  await Promise.race([ready.then(() => undefined).catch(() => undefined), new Promise<void>((resolve) => setTimeout(resolve, INTRO_TIMING.fontWaitMs))]);
}

export interface Waiter {
  promise: Promise<void>;
  cancel(): void;
}

/**
 * Resolves when the screen underneath is there and has stopped changing for `quietMs` (the auth check resolved and
 * the login or start screen rendered), or after `capMs` whatever is there. Never rejects.
 */
export function waitForScreen(capMs: number, quietMs: number): Waiter {
  let finish: () => void = () => undefined;
  let stop: () => void = () => undefined;
  const promise = new Promise<void>((resolve) => {
    let quiet: ReturnType<typeof setTimeout> | undefined;
    const observer = new MutationObserver(() => arm());
    const hasScreen = () => document.querySelector(SCREEN_LANDMARKS) !== null || collectTargets(document.body).length > 0;
    const cap = setTimeout(() => finish(), capMs);
    function arm(): void {
      clearTimeout(quiet);
      if (hasScreen()) quiet = setTimeout(() => finish(), quietMs);
    }
    stop = () => {
      clearTimeout(quiet);
      clearTimeout(cap);
      observer.disconnect();
    };
    finish = () => {
      stop();
      resolve();
    };
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    arm();
  });
  return { promise, cancel: () => stop() };
}

/**
 * The elements the dots land on: marked and in view, and not already held by another reveal (a screen header
 * reveals itself; the intro leaves it alone rather than fight over its `data-fx`).
 */
export function claimTargets(): HTMLElement[] {
  return collectTargets(document.body).filter((el) => !el.hasAttribute(FX_ATTRIBUTE));
}

export function setPhase(elements: readonly HTMLElement[], phase: "wait" | "in" | null): void {
  for (const el of elements) {
    if (phase) el.setAttribute(FX_ATTRIBUTE, phase);
    else el.removeAttribute(FX_ATTRIBUTE);
  }
}

export function toneOf(el: Element): Tone {
  const value = el.getAttribute(TONE_ATTRIBUTE);
  if (value === "muted") return TONE.muted;
  if (value === "accent") return TONE.accent;
  return TONE.ink;
}
