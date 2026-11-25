/**
 * Reveals `[data-particle-target]` elements: the element is held at `opacity: 0` (it keeps its box, so nothing
 * shifts), dots fly to where its glyphs, rule or edges are, and the real element fades in at about 78% of the
 * flight. The last revealed points seed the next reveal, so a new screen's heading forms out of the old one's dots.
 *
 * Framework-free. The React side (components/fx) only gives it a canvas and calls `reveal` from effects.
 */
import { FLIGHT_JITTER_MS, FLIGHT_MIN_MS, HANDOFF_AT, REVEAL_FADE_MS } from "./motion";
import { ParticleLayer, DEFAULT_MAX_DOTS, type AssembleGroup } from "./particles";
import { fxPrefs, type FxPrefs } from "./prefs";
import { capTotal, concat, pointCount, sampleTarget, targetKind, thin, type Points } from "./sample";
import { TONE, type Tone } from "./tone";

export const TARGET_SELECTOR = "[data-particle-target]";
export const FX_ATTRIBUTE = "data-fx";
const TONE_ATTRIBUTE = "data-particle-tone";

const DEFAULT_MAX_REVEAL_DOTS = 1500;
/** Elements of one reveal start this much apart, ms. */
const STAGGER_MS = 30;
/** Fonts that are not ready after this long are sampled as they are. */
const FONT_WAIT_MS = 1200;
/** Whatever happens, hidden elements are shown again after this long. */
const SAFETY_MS = 4000;
const SEED_MAX_POINTS = 1500;
/** New dots with no previous screen to leave from start inside this share of the shorter viewport side. */
const SCATTER_SHARE = 0.3;

export interface RevealOptions {
  /** Flight time, ms (default 520). */
  duration?: number;
  /** Most dots for this reveal (default 1,500). */
  maxDots?: number;
}

export interface RevealHandle {
  /** Shows the elements again at once and drops the dots. Call it when the screen goes away. */
  cancel(): void;
  /** Resolves when the real elements are visible (also after `cancel`). */
  done: Promise<void>;
}

export interface FxController {
  /** The canvas the dots are drawn on (the provider passes its element here). */
  setCanvas(el: HTMLCanvasElement | null): void;
  reveal(root: HTMLElement, options?: RevealOptions): RevealHandle;
  /** The shared full-screen layer, created on first use. `null` when there is no canvas or effects are off. */
  layer(): ParticleLayer | null;
  /** Remember these points as "the departing screen": the next reveal starts from them. */
  remember(points: Points): void;
  /** Cancels everything and frees the layer. The controller can be used again afterwards. */
  detach(): void;
}

interface Job {
  targets: HTMLElement[];
  options: RevealOptions;
  isCancelled: boolean;
  isFinished: boolean;
  settle: () => void;
  done: Promise<void>;
}

const NOOP_HANDLE: RevealHandle = { cancel: () => undefined, done: Promise.resolve() };

function toneOf(el: Element): Tone {
  const value = el.getAttribute(TONE_ATTRIBUTE);
  if (value === "muted") return TONE.muted;
  if (value === "accent") return TONE.accent;
  return TONE.ink;
}

/** Elements to reveal under `root` (and `root` itself) that are in view and have a box. */
export function collectTargets(root: HTMLElement): HTMLElement[] {
  const found = [...root.querySelectorAll<HTMLElement>(TARGET_SELECTOR)];
  if (root.matches(TARGET_SELECTOR)) found.unshift(root);
  return found.filter((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth;
  });
}

function fontsReady(): Promise<void> {
  const ready = document.fonts?.ready;
  if (!ready) return Promise.resolve();
  return Promise.race([ready.then(() => undefined), new Promise<void>((resolve) => setTimeout(resolve, FONT_WAIT_MS))]);
}

/** The job that last claimed an element: an older job must not show an element a newer one is still hiding. */
const owners = new WeakMap<HTMLElement, Job>();

function setPhase(job: Job, phase: "wait" | "in" | null): void {
  for (const el of job.targets) {
    if (owners.get(el) !== job) continue;
    if (phase) el.setAttribute(FX_ATTRIBUTE, phase);
    else el.removeAttribute(FX_ATTRIBUTE);
  }
}

export function createFxController(prefs: FxPrefs = fxPrefs): FxController {
  let canvas: HTMLCanvasElement | null = null;
  const getCanvas = () => canvas;
  let layer: ParticleLayer | null = null;
  let seed: Points = new Float32Array(0);
  let batch: Job[] = [];
  let flushing = false;
  let latest: Job[] = [];
  const timers = new Set<ReturnType<typeof setTimeout>>();
  let unwatch: (() => void) | null = null;

  const later = (fn: () => void, ms: number) => {
    const id = setTimeout(() => {
      timers.delete(id);
      fn();
    }, ms);
    timers.add(id);
  };

  function ensureLayer(): ParticleLayer | null {
    if (layer) return layer;
    if (!canvas || !prefs.getState().moves) return null;
    try {
      layer = new ParticleLayer(canvas, { max: DEFAULT_MAX_DOTS });
    } catch {
      return null;
    }
    unwatch ??= prefs.subscribe(() => {
      if (!prefs.getState().moves) finishAll();
    });
    return layer;
  }

  /** Shows every pending element at once and drops the dots: used when effects go off or anything fails. */
  function finishAll(): void {
    [...batch, ...latest].forEach(finish);
    batch = [];
    latest = [];
    timers.forEach(clearTimeout);
    timers.clear();
    layer?.destroy();
    layer = null;
  }

  function finish(job: Job): void {
    job.isFinished = true;
    setPhase(job, null);
    job.settle();
  }

  function show(job: Job): void {
    if (!job.isCancelled && !job.isFinished) setPhase(job, "in");
  }

  function seedFrom(points: Points, targets: Points): (k: number) => readonly [number, number] {
    const n = pointCount(points);
    if (n > 0) return (k) => [points[(k % n) * 2], points[(k % n) * 2 + 1]];
    // No previous screen: gather around the middle of the targets.
    const m = pointCount(targets) || 1;
    let cx = 0;
    let cy = 0;
    for (let i = 0; i < m; i += 1) {
      cx += targets[i * 2];
      cy += targets[i * 2 + 1];
    }
    const radius = Math.min(window.innerWidth, window.innerHeight) * SCATTER_SHARE;
    return () => [cx / m + (Math.random() * 2 - 1) * radius, cy / m + (Math.random() * 2 - 1) * radius];
  }

  /** Samples one job's elements into groups, capped at the job's own dot budget. */
  function sampleJob(job: Job, offset: number): { groups: AssembleGroup[]; all: Points } {
    const max = job.options.maxDots ?? DEFAULT_MAX_REVEAL_DOTS;
    const lists = job.targets.map((el) => sampleTarget(el, targetKind(el), max));
    const capped = capTotal(lists, max);
    const groups = capped.map((points, i) => ({ points, tone: toneOf(job.targets[i]), delay: (offset + i) * STAGGER_MS }));
    return { groups, all: concat(capped) };
  }

  async function flush(): Promise<void> {
    if (flushing) return;
    flushing = true;
    await fontsReady();
    flushing = false;
    const jobs = batch.filter((job) => !job.isCancelled);
    batch = [];
    if (!jobs.length) return;
    // A newer reveal supersedes one still in flight: that one is shown at once.
    latest.filter((job) => !jobs.includes(job)).forEach(finish);
    const target = ensureLayer();
    if (!target || !prefs.getState().moves) {
      jobs.forEach(finish);
      return;
    }
    latest = jobs;
    play(target, jobs);
  }

  function play(target: ParticleLayer, jobs: Job[]): void {
    let offset = 0;
    const groups: AssembleGroup[] = [];
    const landed: Points[] = [];
    for (const job of jobs) {
      const sampled = sampleJob(job, offset);
      groups.push(...sampled.groups);
      landed.push(sampled.all);
      offset += job.targets.length;
    }
    const all = concat(landed);
    if (!pointCount(all)) {
      jobs.forEach(finish);
      return;
    }
    const duration = Math.max(FLIGHT_MIN_MS, Math.min(...jobs.map((j) => j.options.duration ?? FLIGHT_MIN_MS)));
    const from = seedFrom(seed, all);
    seed = thin(all, SEED_MAX_POINTS);
    void target.fadeTo(1, 1);
    const reach = duration + FLIGHT_JITTER_MS + offset * STAGGER_MS;
    later(() => jobs.forEach(show), reach * HANDOFF_AT);
    later(() => jobs.forEach(finish), SAFETY_MS);
    void target.assemble(groups, { duration, jitter: FLIGHT_JITTER_MS, from }).then(async () => {
      jobs.forEach(show);
      await target.fadeTo(0, REVEAL_FADE_MS);
      // Only the newest reveal may clear the layer.
      if (latest === jobs) {
        target.clear();
        latest = [];
      }
      later(() => jobs.forEach(finish), REVEAL_FADE_MS);
    });
  }

  return {
    setCanvas(el) {
      if (el === canvas) return;
      layer?.destroy();
      layer = null;
      canvas = el;
    },
    reveal(root, options = {}) {
      if (!prefs.getState().moves || !getCanvas()) return NOOP_HANDLE;
      const targets = collectTargets(root);
      if (!targets.length) return NOOP_HANDLE;
      let settle: () => void = () => undefined;
      const done = new Promise<void>((resolve) => (settle = resolve));
      const job: Job = { targets, options, isCancelled: false, isFinished: false, settle, done };
      targets.forEach((el) => owners.set(el, job));
      setPhase(job, "wait");
      batch.push(job);
      void flush();
      return {
        done,
        cancel() {
          job.isCancelled = true;
          finish(job);
          if (latest.length && latest.every((j) => j.isCancelled)) {
            layer?.clear();
            latest = [];
          }
        },
      };
    },
    layer: ensureLayer,
    remember(points) {
      seed = thin(points, SEED_MAX_POINTS);
    },
    detach() {
      finishAll();
      unwatch?.();
      unwatch = null;
    },
  };
}
