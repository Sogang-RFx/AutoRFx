/**
 * What every effect canvas shares: DPR-capped sizing, the frame loop (stops when the tab is hidden, the canvas is
 * offscreen or the frame has nothing left to do), a pointer tracker and the dot colours read from the CSS tokens.
 */
import { POINTER_DECAY } from "./motion";
import { TONE_TOKENS } from "./tone";

export const DPR_CAP = 2;
/** A frame step is never longer than this, so a stalled tab does not make dots jump. */
export const MAX_FRAME_S = 0.05;
const POINTER_GAP_MIN_MS = 8;
const POINTER_VELOCITY_MIX = 0.5;
/** Used when a token is missing: the canvas element's own text colour, never a fixed colour. */
const COLOR_FALLBACK = "currentcolor";

/** Ink, muted, accent, danger: read from the CSS tokens, in `TONE` order. */
export function readToneColors(root: Element = document.documentElement): string[] {
  const style = getComputedStyle(root);
  return TONE_TOKENS.map((name) => style.getPropertyValue(name).trim() || COLOR_FALLBACK);
}

export interface PointerState {
  /** Viewport px. */
  x: number;
  y: number;
  /** Px per second, decays by itself. */
  vx: number;
  vy: number;
  on: boolean;
  at: number;
}

export const newPointer = (): PointerState => ({ x: 0, y: 0, vx: 0, vy: 0, on: false, at: 0 });

export function decayPointer(pointer: PointerState, dt: number): void {
  const keep = Math.max(0, 1 - dt * POINTER_DECAY);
  pointer.vx *= keep;
  pointer.vy *= keep;
}

/** Follows the mouse, pen and touch over the whole window. Returns the function that removes the listeners. */
export function trackPointer(state: PointerState, onMove?: () => void): () => void {
  const move = (e: PointerEvent) => {
    const now = performance.now();
    if (state.on) {
      const gap = Math.max(POINTER_GAP_MIN_MS, now - state.at) / 1000;
      state.vx += ((e.clientX - state.x) / gap - state.vx) * POINTER_VELOCITY_MIX;
      state.vy += ((e.clientY - state.y) / gap - state.vy) * POINTER_VELOCITY_MIX;
    }
    state.x = e.clientX;
    state.y = e.clientY;
    state.at = now;
    state.on = true;
    onMove?.();
  };
  // Letting go also wakes a stopped loop, so pushed dots can spring back.
  const end = (e: PointerEvent) => {
    if (e.pointerType === "mouse") return;
    state.on = false;
    onMove?.();
  };
  const leave = (e: MouseEvent) => {
    if (e.relatedTarget) return;
    state.on = false;
    onMove?.();
  };
  window.addEventListener("pointermove", move, { passive: true });
  window.addEventListener("pointerdown", move, { passive: true });
  window.addEventListener("pointerup", end, { passive: true });
  window.addEventListener("pointercancel", end, { passive: true });
  window.addEventListener("mouseout", leave, { passive: true });
  return () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerdown", move);
    window.removeEventListener("pointerup", end);
    window.removeEventListener("pointercancel", end);
    window.removeEventListener("mouseout", leave);
  };
}

export interface CanvasSize {
  /** CSS px. */
  w: number;
  h: number;
  dpr: number;
}

export interface CanvasHost {
  readonly size: CanvasSize;
  readonly pointer: PointerState;
  /** Starts the loop if it is stopped and there is a reason to run. */
  wake(): void;
  /** Runs one frame now with a zero step (a still image). */
  frameOnce(): void;
  dispose(): void;
}

export interface HostOptions {
  /** Track the pointer (default true). */
  pointer?: boolean;
  /** Asked on every pointer event: false keeps the loop asleep (the pointer is still tracked). Default: always wake. */
  wakesOnPointer?: () => boolean;
}

/**
 * `frame(dt)` draws one frame (dt in seconds) and returns whether another one is needed. The loop is only a
 * `requestAnimationFrame` chain while the tab is visible and the canvas intersects the viewport.
 */
export function createCanvasHost(canvas: HTMLCanvasElement, frame: (dt: number) => boolean, options: HostOptions = {}): CanvasHost {
  const size: CanvasSize = { w: 0, h: 0, dpr: 1 };
  const pointer = newPointer();
  let raf = 0;
  let last = 0;
  let isVisible = true;
  let isDisposed = false;

  const canRun = () => !isDisposed && isVisible && !document.hidden;

  function tick(now: number): void {
    raf = 0;
    if (!canRun()) return;
    const dt = last ? Math.min(MAX_FRAME_S, (now - last) / 1000) : 0;
    last = now;
    if (frame(dt)) raf = requestAnimationFrame(tick);
    else last = 0;
  }

  function wake(): void {
    if (raf || !canRun()) return;
    last = 0;
    raf = requestAnimationFrame(tick);
  }

  function fit(): void {
    const rect = canvas.getBoundingClientRect();
    size.dpr = Math.min(DPR_CAP, window.devicePixelRatio || 1);
    size.w = rect.width;
    size.h = rect.height;
    // Assigning the same size still wipes the canvas, which would erase a still picture on a redundant refit.
    const width = Math.round(rect.width * size.dpr);
    const height = Math.round(rect.height * size.dpr);
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
  }

  fit();
  const resizer = new ResizeObserver(() => {
    fit();
    wake();
  });
  resizer.observe(canvas);
  const watcher = new IntersectionObserver(([entry]) => {
    isVisible = entry.isIntersecting;
    wake();
  });
  watcher.observe(canvas);
  const onVisibility = () => wake();
  document.addEventListener("visibilitychange", onVisibility);
  const untrack = options.pointer === false ? () => undefined : trackPointer(pointer, () => {
        if (options.wakesOnPointer?.() ?? true) wake();
      });

  return {
    size,
    pointer,
    wake,
    frameOnce() {
      frame(0);
    },
    dispose() {
      isDisposed = true;
      cancelAnimationFrame(raf);
      raf = 0;
      resizer.disconnect();
      watcher.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      untrack();
    },
  };
}
