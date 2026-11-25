/**
 * The intro's choreography (docs/design/immersive-ui.md §4.1), without React: a sphere of dots gathers, the same
 * dots become the name and the one-line description, then fly to the real screen underneath, whose elements fade
 * in over them while the cover lifts.
 *
 * `runIntro` returns at once. Whatever happens (skip, resize, effects turned off, any error, `cancel` from a
 * Strict Mode cleanup) everything it touched is put back: canvases emptied, listeners removed, hidden elements shown.
 */
import { easeFlight, HANDOFF_AT, REVEAL_FADE_MS } from "@/lib/fx/motion";
import { DEFAULT_MAX_DOTS, ParticleLayer, type AssembleGroup } from "@/lib/fx/particles";
import { fxPrefs } from "@/lib/fx/prefs";
import { capTotal, concat, pointCount, sampleTarget, sampleText, targetKind, type Points } from "@/lib/fx/sample";
import { ParticleSphere } from "@/lib/fx/sphere";
import { TONE } from "@/lib/fx/tone";
import { claimTargets, fontsReady, prepareFonts, probeFamilies, setPhase, toneOf, waitForScreen } from "./intro-dom";
import { handoffReach, INTRO_TIMING, introBudget, nameLayout, resizeEndsIntro, WORDMARK, type NameLayout } from "./intro-plan";

export type IntroEnd = "done" | "skipped" | "resized" | "disabled" | "timeout" | "failed";

export interface IntroHooks {
  sphereCanvas: HTMLCanvasElement;
  layerCanvas: HTMLCanvasElement;
  /** The page-colour sheet; `data-fx="out"` lifts it. */
  cover: HTMLElement;
  /** The hand-off starts: the skip button is no longer needed. */
  onHandoff?: () => void;
  /** Where the last dots landed, for the next screen's reveal to start from. */
  remember?: (points: Points) => void;
  /** Called once when the intro is over for any reason except `cancel`. The cover can go. */
  onEnd: (reason: IntroEnd) => void;
}

export interface IntroRun {
  /** Shows the real screen at once. */
  skip(): void;
  /** Stops without reporting an end (the component is being unmounted or remounted). */
  cancel(): void;
}

const SPHERE_RADIUS_SHARE = 0.2;
const WORD_WEIGHT = 600;
const TAG_WEIGHT = 500;
const WORD_STEP = 3;
const TAG_STEP = 1.5;
/** Share of the name's dots that make the word; the rest make the description. */
const WORD_SHARE = 0.7;
const COVER_OUT = "out";

const at = (points: Points, k: number): readonly [number, number] => {
  const n = pointCount(points);
  return n ? [points[(k % n) * 2], points[(k % n) * 2 + 1]] : [0, 0];
};

export function runIntro(hooks: IntroHooks): IntroRun {
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const cleanups: Array<() => void> = [];
  const claimed: HTMLElement[] = [];
  let isDead = false;
  let morphFrame = 0;
  let sphere: ParticleSphere | null = null;
  let layer: ParticleLayer | null = null;

  const sleep = (ms: number) =>
    new Promise<void>((resolve) => {
      const id = setTimeout(() => {
        timers.delete(id);
        resolve();
      }, ms);
      timers.add(id);
    });
  const later = (fn: () => void, ms: number) => void sleep(ms).then(() => !isDead && fn());

  function teardown(): void {
    isDead = true;
    timers.forEach(clearTimeout);
    timers.clear();
    cancelAnimationFrame(morphFrame);
    cleanups.splice(0).forEach((fn) => fn());
    sphere?.destroy();
    layer?.destroy();
    setPhase(claimed, null);
  }

  function end(reason: IntroEnd): void {
    if (isDead) return;
    teardown();
    hooks.onEnd(reason);
  }

  function guard(): void {
    const view = { w: window.innerWidth, h: window.innerHeight };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && end("skipped");
    const onResize = () => resizeEndsIntro(view, { w: window.innerWidth, h: window.innerHeight }) && end("resized");
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", onResize);
    const unsubscribe = fxPrefs.subscribe(() => !fxPrefs.getState().moves && end("disabled"));
    cleanups.push(() => {
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", onResize);
      unsubscribe();
    });
    later(() => end("timeout"), INTRO_TIMING.hardCapMs);
  }

  /** Phase 1: dots gather into the sphere. */
  function startSphere(count: number): void {
    sphere = new ParticleSphere(hooks.sphereCanvas, { count, radiusShare: SPHERE_RADIUS_SHARE });
    sphere.setMode("idle");
    sphere.setMorph(0);
    const born = performance.now();
    const step = () => {
      const k = (performance.now() - born) / INTRO_TIMING.morphMs;
      sphere?.setMorph(easeFlight(k));
      if (k < 1) morphFrame = requestAnimationFrame(step);
    };
    morphFrame = requestAnimationFrame(step);
    later(() => sphere?.pulse(), INTRO_TIMING.pulseAtMs);
  }

  /** The name and the description as dots, laid out for this viewport. */
  function nameGroups(layout: NameLayout, wordFont: string, tagFont: string, budget: number): AssembleGroup[] {
    const word = sampleText(WORDMARK, {
      font: wordFont,
      size: layout.wordSize,
      cx: layout.cx,
      cy: layout.wordY,
      letterSpacing: `${layout.wordSpacingPx}px`,
      step: WORD_STEP,
      max: Math.floor(budget * WORD_SHARE),
    });
    const lines = layout.tagLines.map((line) =>
      sampleText(line.text, { font: tagFont, size: layout.tagSize, cx: layout.cx, cy: line.y, step: TAG_STEP }),
    );
    const [description] = capTotal([concat(lines)], Math.floor(budget * (1 - WORD_SHARE)));
    return [
      { points: word, tone: TONE.ink },
      { points: description, tone: TONE.muted, delay: INTRO_TIMING.taglineDelayMs },
    ];
  }

  /** Phase 2: the sphere's dots fly to the name. */
  async function nameStage(budget: number): Promise<void> {
    const families = probeFamilies();
    const layout = nameLayout(window.innerWidth, window.innerHeight);
    const wordFont = `${WORD_WEIGHT} ${layout.wordSize}px ${families.serif}`;
    const tagFont = `${TAG_WEIGHT} ${layout.tagSize}px ${families.sans}`;
    await Promise.all([sleep(INTRO_TIMING.sphereMs), prepareFonts(wordFont, tagFont)]);
    if (isDead || !sphere) return;
    const start = sphere.exportPoints();
    layer = new ParticleLayer(hooks.layerCanvas, { max: DEFAULT_MAX_DOTS });
    const landed = layer.assemble(nameGroups(layout, wordFont, tagFont, budget), {
      duration: INTRO_TIMING.nameFlightMs,
      jitter: INTRO_TIMING.nameJitterMs,
      from: (k) => at(start, k),
    });
    // The layer draws the same dots from the next frame on: the sphere is dropped once it has.
    requestAnimationFrame(() => requestAnimationFrame(() => sphere?.destroy()));
    await landed;
    if (!isDead) await sleep(INTRO_TIMING.holdMs);
  }

  function handoffGroups(elements: readonly HTMLElement[], budget: number): AssembleGroup[] {
    const lists = elements.map((el) => sampleTarget(el, targetKind(el), budget));
    return capTotal(lists, budget).map((points, i) => ({
      points,
      tone: toneOf(elements[i]),
      delay: i * INTRO_TIMING.handoffStaggerMs,
    }));
  }

  /** Phase 3: dots fly to the real screen, its elements fade in at about 78%, the cover lifts. */
  async function handoffStage(budget: number): Promise<void> {
    if (isDead || !layer) return;
    const screen = waitForScreen(INTRO_TIMING.screenWaitMs, INTRO_TIMING.screenQuietMs);
    cleanups.push(screen.cancel);
    await screen.promise;
    await fontsReady();
    if (isDead || !layer) return;
    hooks.onHandoff?.();
    claimed.push(...claimTargets());
    setPhase(claimed, "wait");
    const from = layer.exportPoints();
    const groups = handoffGroups(claimed, budget);
    hooks.cover.setAttribute("data-fx", COVER_OUT);
    if (!groups.some((g) => pointCount(g.points) > 0)) {
      // Nothing to land on (a screen without marked elements): the name simply dissolves.
      await layer.fadeTo(0, REVEAL_FADE_MS);
      return end("done");
    }
    later(() => setPhase(claimed, "in"), handoffReach(claimed.length) * HANDOFF_AT);
    await layer.assemble(groups, {
      duration: INTRO_TIMING.handoffMs,
      jitter: INTRO_TIMING.handoffJitterMs,
      from: (k) => at(from, k),
    });
    if (isDead || !layer) return;
    setPhase(claimed, "in");
    hooks.remember?.(layer.exportPoints());
    await layer.fadeTo(0, REVEAL_FADE_MS);
    end("done");
  }

  async function play(): Promise<void> {
    const budget = introBudget(navigator.hardwareConcurrency, window.innerWidth);
    startSphere(budget.sphere);
    await nameStage(budget.name);
    await handoffStage(budget.handoff);
  }

  guard();
  play().catch(() => end("failed"));
  return { skip: () => end("skipped"), cancel: teardown };
}
