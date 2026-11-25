/**
 * One canvas of crisp 1 to 2 px dots, kept in typed arrays (struct of arrays). Coordinates are canvas CSS px:
 * on the full-screen fixed layer that is the viewport.
 *
 * Modes of a dot: DRIFT (floats about), WAIT (floats until its turn), TRAVEL (on its way to a target),
 * HOLD (landed), FLOW (follows a `FlowField`). The loop runs only while something moves.
 */
import { createCanvasHost, decayPointer, readToneColors, type CanvasHost } from "./canvas-kit";
import { FlowField, type FlowLane, type FlowOptions, type FlowOut } from "./flow";
import {
  approach,
  BEND_MAX,
  flightPoint,
  FLIGHT_JITTER_MS,
  FLIGHT_MAX_MS,
  repelForce,
  type Vec2,
} from "./motion";
import { pointCount, thin, type Points, type Random } from "./sample";
import { MUTED_SHARE, TONE, type Tone } from "./tone";

export const MODE = { drift: 0, wait: 1, travel: 2, hold: 3, flow: 4 } as const;

export const DEFAULT_MAX_DOTS = 9000;
/** Surplus dots fade over this long and are then dropped. */
const LEAVE_MS = 420;
const DRIFT_SPEED_PX = 8;
const DOT_SIZES = [1, 1.5, 2] as const;
const ALPHA_MIN = 0.45;
const ALPHA_SPAN = 0.45;
const ALPHA_CUTOFF = 0.02;
const FLOW_ALPHA = 0.7;
const SETTLED_PX = 0.01;

export interface AssembleGroup {
  /** x, y pairs. */
  points: Points;
  /** Extra wait before this group's dots start, ms. */
  delay?: number;
  /** Tone of the dots; default: each dot keeps its own ink or muted tone. */
  tone?: Tone;
}

export interface AssembleOptions {
  /** Flight time of one dot, ms. */
  duration?: number;
  /** Each dot waits up to this much longer, ms. */
  jitter?: number;
  /** Where the k-th dot that has to be added starts; default: anywhere on the canvas. */
  from?: (k: number) => readonly [number, number];
}

export interface LayerOptions {
  max?: number;
  random?: Random;
  /** Defaults to the CSS tokens `--color-ink`, `-ink-muted`, `-accent`, `-danger`. */
  colors?: readonly string[];
}

export interface FlowSpec extends FlowOptions {
  count: number;
  lanes: readonly FlowLane[];
}

export interface FlowHandle {
  /** New gate states or positions: dots move on from where they are. */
  update(lanes: readonly FlowLane[]): void;
  /** Dots drift again. */
  stop(): void;
}

interface Travel {
  start: number;
  duration: number;
  end: number;
  resolve: () => void;
}

interface Fade {
  from: number;
  to: number;
  start: number;
  ms: number;
  resolve: () => void;
}

/** Whether the layer needs another frame. With no dots the answer is always no. */
export function layerNeedsFrame(s: { dots: number; moving: boolean; fading: boolean }): boolean {
  return s.dots > 0 && (s.moving || s.fading);
}

/** The pointer can only push dots that exist, so with none it must not wake the loop. */
export const layerWakesOnPointer = (dots: number): boolean => dots > 0;

export class ParticleLayer {
  readonly max: number;
  private readonly random: Random;
  private readonly colors: readonly string[];
  private readonly ctx: CanvasRenderingContext2D;
  private readonly host: CanvasHost;
  private n = 0;
  private time = 0;
  private fade = 1;
  private travel: Travel | null = null;
  private fading: Fade | null = null;
  private leaving: { from: number; start: number } | null = null;
  private field: FlowField | null = null;
  private isDestroyed = false;
  private readonly flowOut: FlowOut = { x: 0, y: 0, a: 1, tone: TONE.ink };
  private readonly spot: Vec2 = { x: 0, y: 0 };
  private readonly push: Vec2 = { x: 0, y: 0 };

  private readonly x: Float32Array;
  private readonly y: Float32Array;
  private readonly sx: Float32Array;
  private readonly sy: Float32Array;
  private readonly tx: Float32Array;
  private readonly ty: Float32Array;
  private readonly ox: Float32Array;
  private readonly oy: Float32Array;
  private readonly vx: Float32Array;
  private readonly vy: Float32Array;
  private readonly bend: Float32Array;
  private readonly delay: Float32Array;
  private readonly size: Float32Array;
  private readonly alpha: Float32Array;
  private readonly shown: Float32Array;
  private readonly phase: Float32Array;
  private readonly tone: Uint8Array;
  private readonly base: Uint8Array;
  private readonly mode: Uint8Array;

  constructor(
    readonly canvas: HTMLCanvasElement,
    options: LayerOptions = {},
  ) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("2D canvas is not available");
    this.ctx = ctx;
    this.max = options.max ?? DEFAULT_MAX_DOTS;
    this.random = options.random ?? Math.random;
    this.colors = options.colors ?? readToneColors();
    const floats = () => new Float32Array(this.max);
    [this.x, this.y, this.sx, this.sy, this.tx, this.ty, this.ox, this.oy] = [floats(), floats(), floats(), floats(), floats(), floats(), floats(), floats()];
    [this.vx, this.vy, this.bend, this.delay, this.size, this.alpha, this.shown, this.phase] = [floats(), floats(), floats(), floats(), floats(), floats(), floats(), floats()];
    this.tone = new Uint8Array(this.max);
    this.base = new Uint8Array(this.max);
    this.mode = new Uint8Array(this.max);
    this.host = createCanvasHost(canvas, (dt) => this.frame(dt), { wakesOnPointer: () => layerWakesOnPointer(this.n) });
  }

  /** Number of live dots. */
  get dotCount(): number {
    return this.n;
  }

  /** Adds up to `count` drifting dots at `place(k)`. */
  private spawn(count: number, place: (k: number) => readonly [number, number]): void {
    const first = this.n;
    const end = Math.min(this.max, first + Math.max(0, Math.floor(count)));
    for (let i = first; i < end; i += 1) {
      const [px, py] = place(i - first);
      this.x[i] = px;
      this.y[i] = py;
      this.ox[i] = 0;
      this.oy[i] = 0;
      this.vx[i] = (this.random() - 0.5) * 2 * DRIFT_SPEED_PX;
      this.vy[i] = (this.random() - 0.5) * 2 * DRIFT_SPEED_PX;
      this.size[i] = DOT_SIZES[Math.floor(this.random() * DOT_SIZES.length)];
      this.alpha[i] = ALPHA_MIN + this.random() * ALPHA_SPAN;
      this.phase[i] = this.random() * Math.PI * 2;
      this.base[i] = this.random() < MUTED_SHARE ? TONE.muted : TONE.ink;
      this.tone[i] = this.base[i];
      this.mode[i] = MODE.drift;
    }
    this.n = end;
    this.host.wake();
  }

  /** Drifting dots at the given positions (for example `sphere.exportPoints()`), added to the live ones. */
  seed(points: Points): void {
    this.spawn(pointCount(points), (k) => [points[k * 2], points[k * 2 + 1]]);
  }

  /** `count` dots anywhere on the canvas. */
  scatter(count: number): void {
    this.spawn(count, this.anywhere);
  }

  private readonly anywhere = (): readonly [number, number] => [this.random() * this.host.size.w, this.random() * this.host.size.h];

  /**
   * Sends dots to the target points. Live dots are reused and fly from where they are; missing ones are added at
   * `from(k)`; surplus ones fade out. Each dot waits its group's delay plus up to `jitter` ms. More points than
   * `max` are thinned evenly. Resolves when the last dot has landed (or when another call takes over).
   */
  assemble(groups: readonly AssembleGroup[], options: AssembleOptions = {}): Promise<void> {
    const { duration = FLIGHT_MAX_MS, jitter = FLIGHT_JITTER_MS, from = this.anywhere } = options;
    this.field = null;
    const wanted = groups.reduce((sum, g) => sum + pointCount(g.points), 0);
    const budget = Math.min(wanted, this.max);
    if (this.n < budget) this.spawn(budget - this.n, from);
    const share = wanted > 0 ? budget / wanted : 0;
    let i = 0;
    let latest = 0;
    for (const group of groups) {
      const points = share < 1 ? thin(group.points, Math.floor(pointCount(group.points) * share), this.random) : group.points;
      for (let k = 0; k < pointCount(points) && i < this.n; k += 1, i += 1) {
        this.tx[i] = points[k * 2];
        this.ty[i] = points[k * 2 + 1];
        this.delay[i] = (group.delay ?? 0) + this.random() * jitter;
        this.bend[i] = (this.random() * 2 - 1) * BEND_MAX;
        this.tone[i] = group.tone ?? this.base[i];
        this.mode[i] = MODE.wait;
        latest = Math.max(latest, this.delay[i]);
      }
    }
    this.leaving = i < this.n ? { from: i, start: this.time } : null;
    this.travel?.resolve();
    this.travel = null;
    this.host.wake();
    return new Promise<void>((resolve) => {
      this.travel = { start: this.time, duration, end: latest + duration, resolve };
    });
  }

  /**
   * Replaces the dots with `count` that stream through the lanes' gates (see flow.ts for what each gate state does).
   * `update` changes states live; `stop` lets the dots drift.
   */
  flow(spec: FlowSpec): FlowHandle {
    this.n = 0;
    this.leaving = null;
    this.travel?.resolve();
    this.travel = null;
    this.spawn(spec.count, () => [0, 0]);
    const field = new FlowField(this.n, this.random, spec);
    field.setLanes(spec.lanes);
    this.field = field;
    this.mode.fill(MODE.flow, 0, this.n);
    this.host.wake();
    return {
      update: (lanes) => {
        field.setLanes(lanes);
        this.host.wake();
      },
      stop: () => {
        if (this.field !== field) return;
        this.field = null;
        for (let i = 0; i < this.n; i += 1) if (this.mode[i] === MODE.flow) this.mode[i] = MODE.drift;
      },
    };
  }

  /** Lets go of the targets: the dots drift again from where they are. */
  release(): void {
    for (let i = 0; i < this.n; i += 1) this.mode[i] = MODE.drift;
    this.field = null;
    this.travel?.resolve();
    this.travel = null;
    this.host.wake();
  }

  /** Fades the whole layer to `target` (0..1) over `ms`. */
  fadeTo(target: number, ms: number): Promise<void> {
    this.fading?.resolve();
    this.host.wake();
    return new Promise<void>((resolve) => {
      this.fading = { from: this.fade, to: target, start: this.time, ms: Math.max(1, ms), resolve };
    });
  }

  /** Removes every dot at once and empties the canvas. */
  clear(): void {
    this.n = 0;
    this.leaving = null;
    this.field = null;
    this.fade = 1;
    this.travel?.resolve();
    this.travel = null;
    this.fading?.resolve();
    this.fading = null;
    this.ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  /** Current positions of the live dots, x, y pairs. */
  exportPoints(): Points {
    const out = new Float32Array(this.n * 2);
    for (let i = 0; i < this.n; i += 1) {
      out[i * 2] = this.x[i];
      out[i * 2 + 1] = this.y[i];
    }
    return out;
  }

  /** Stops the loop, removes the listeners and empties the canvas. Safe to call twice. */
  destroy(): void {
    if (this.isDestroyed) return;
    this.isDestroyed = true;
    this.host.dispose();
    this.clear();
  }

  // ── Frame ──────────────────────────────────────────────────────────────────────────────────────────────────────

  private frame(dt: number): boolean {
    if (this.isDestroyed) return false;
    this.time += dt * 1000;
    // No dots: nothing to move and nothing to draw (the canvas was emptied when the last dot went).
    if (this.n === 0) return false;
    const moving = this.step(dt);
    this.draw();
    // Nothing moving and nothing fading: the loop stops until something calls wake().
    return layerNeedsFrame({ dots: this.n, moving, fading: this.fading !== null });
  }

  /** Advances every dot. Returns whether anything is still moving. */
  private step(dt: number): boolean {
    const { pointer, size } = this.host;
    const rect = this.canvas.getBoundingClientRect();
    const px = pointer.x - rect.left;
    const py = pointer.y - rect.top;
    const seconds = this.time / 1000;
    decayPointer(pointer, dt);
    this.stepFade();
    const leave = this.leaving ? 1 - (this.time - this.leaving.start) / LEAVE_MS : 1;
    // Flow dots count as moving only while one streams or is still on its way to a gate (see FlowField.isMoving).
    let moving = this.leaving !== null || this.travel !== null;
    this.field?.startFrame();
    for (let i = 0; i < this.n; i += 1) {
      let alpha = this.alpha[i];
      if (this.mode[i] === MODE.flow && this.field) alpha = FLOW_ALPHA * this.stepFlow(i, dt);
      else this.stepFlight(i, dt, seconds, size.w, size.h);
      if (this.leaving && i >= this.leaving.from) alpha *= Math.max(0, leave);
      const hit = pointer.on ? this.push : null;
      if (hit) repelForce(hit, this.x[i] - px, this.y[i] - py, pointer.vx, pointer.vy);
      const goalX = hit ? hit.x : 0;
      const goalY = hit ? hit.y : 0;
      this.ox[i] = approach(this.ox[i], goalX, dt);
      this.oy[i] = approach(this.oy[i], goalY, dt);
      // A dot still springing toward where the pointer wants it keeps the loop alive; one that got there does not.
      if (Math.abs(goalX - this.ox[i]) + Math.abs(goalY - this.oy[i]) > SETTLED_PX) moving = true;
      if (this.mode[i] !== MODE.hold && this.mode[i] !== MODE.flow) moving = true;
      this.shown[i] = alpha * this.fade;
    }
    if (this.field?.isMoving) moving = true;
    this.finishLeaving(leave);
    this.finishTravel();
    return moving;
  }

  private stepFade(): void {
    const fading = this.fading;
    if (!fading) return;
    const k = Math.min(1, (this.time - fading.start) / fading.ms);
    this.fade = fading.from + (fading.to - fading.from) * k;
    if (k < 1) return;
    this.fading = null;
    fading.resolve();
  }

  private stepFlow(i: number, dt: number): number {
    const out = this.flowOut;
    this.field?.advance(i, dt, out);
    this.x[i] = out.x;
    this.y[i] = out.y;
    this.tone[i] = out.tone;
    return out.a;
  }

  private stepFlight(i: number, dt: number, seconds: number, w: number, h: number): void {
    const mode = this.mode[i];
    if (mode === MODE.hold) return;
    const travel = this.travel;
    const t = travel && (mode === MODE.wait || mode === MODE.travel) ? (this.time - travel.start - this.delay[i]) / travel.duration : 0;
    if (mode === MODE.drift || (mode === MODE.wait && t <= 0)) {
      this.x[i] = wrap(this.x[i] + this.vx[i] * dt, w);
      this.y[i] = wrap(this.y[i] + this.vy[i] * dt, h);
      return;
    }
    if (mode === MODE.flow) return;
    if (t >= 1) {
      this.x[i] = this.tx[i];
      this.y[i] = this.ty[i];
      this.mode[i] = MODE.hold;
      return;
    }
    if (mode === MODE.wait) {
      this.sx[i] = this.x[i];
      this.sy[i] = this.y[i];
      this.mode[i] = MODE.travel;
    }
    flightPoint(this.spot, t, this.sx[i], this.sy[i], this.tx[i], this.ty[i], this.bend[i], seconds, this.phase[i]);
    this.x[i] = this.spot.x;
    this.y[i] = this.spot.y;
  }

  private finishLeaving(leave: number): void {
    if (!this.leaving || leave > 0) return;
    this.n = this.leaving.from;
    this.leaving = null;
  }

  private finishTravel(): void {
    const travel = this.travel;
    if (!travel || this.time < travel.start + travel.end) return;
    this.travel = null;
    travel.resolve();
  }

  private draw(): void {
    const { ctx } = this;
    const { w, h, dpr } = this.host.size;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    let lastAlpha = -1;
    for (let tone = 0; tone < this.colors.length; tone += 1) {
      ctx.fillStyle = this.colors[tone];
      for (let i = 0; i < this.n; i += 1) {
        if (this.tone[i] !== tone || this.shown[i] < ALPHA_CUTOFF) continue;
        const a = Math.min(1, this.shown[i]);
        if (a !== lastAlpha) {
          ctx.globalAlpha = a;
          lastAlpha = a;
        }
        const s = this.size[i];
        // Snapped to the device pixel grid, so a dot stays crisp.
        const dx = Math.round((this.x[i] + this.ox[i] - s / 2) * dpr) / dpr;
        const dy = Math.round((this.y[i] + this.oy[i] - s / 2) * dpr) / dpr;
        ctx.fillRect(dx, dy, s, s);
      }
    }
    ctx.globalAlpha = 1;
  }
}

function wrap(value: number, length: number): number {
  if (length <= 0) return value;
  return ((value % length) + length) % length;
}
