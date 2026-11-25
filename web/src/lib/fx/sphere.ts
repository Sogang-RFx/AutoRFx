/**
 * A rotating, breathing sphere of dots on a Fibonacci lattice, drawn on Canvas 2D with perspective and depth
 * shading (docs/design/immersive-ui.md §4.1, §4.4, §6). The maths is pure and tested; `ParticleSphere` draws it.
 * `level` (0..1, a microphone or playback level) swells the sphere and sends ripples outward.
 */
import { createCanvasHost, decayPointer, readToneColors, type CanvasHost } from "./canvas-kit";
import { approach, clamp01, repelForce, seededRandom, smoothstep, type Vec2 } from "./motion";
import type { Points, Random } from "./sample";
import { MUTED_SHARE, TONE } from "./tone";

export type SphereMode = "idle" | "listening" | "thinking" | "speaking";

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
/** Distance of the eye from the centre, in sphere radii: smaller means stronger perspective. */
const FOV = 3.9;
const TILT = 0.2;
const DEPTH_ALPHA_MIN = 0.2;
const DEPTH_ALPHA_SPAN = 0.7;
const DEPTH_SIZE_MIN = 0.7;
const DEPTH_SIZE_SPAN = 0.6;
const DOT_SIZES = [1, 1.5, 2] as const;
/** Dots stay crisp: even a ripple never grows one past this. */
const DOT_MAX_PX = 2;
/** A dot turns accent only under the core of a ripple. */
const RIPPLE_TONE_AT = 0.45;

export const RIPPLE_MS = 1250;
const RIPPLE_REACH = 1.15;
const RIPPLE_WIDTH = 0.16;
const RIPPLES_MAX = 5;
const RIPPLE_GAP_MS = 160;

/** Level smoothing: quick to rise, slow to fall (per second). */
const LEVEL_ATTACK = 22;
const LEVEL_RELEASE = 3.2;
/** The fast level must exceed the slow one by this much to count as a syllable. */
const SPIKE_MARGIN = 0.055;
const SLOW_RATE = 2.2;

export interface ModeLook {
  /** Radians per second about the vertical axis. */
  spin: number;
  breatheAmp: number;
  breatheRate: number;
  /** How much `level` swells the radius. */
  swell: number;
  /** Ripples come from level spikes. */
  rippleOnLevel: boolean;
  /** Ripples are drawn in the accent tone. */
  accent: boolean;
}

export const MODE_LOOK: Record<SphereMode, ModeLook> = {
  idle: { spin: 0.16, breatheAmp: 0.016, breatheRate: 1.36, swell: 0, rippleOnLevel: false, accent: false },
  listening: { spin: 0.1, breatheAmp: 0.01, breatheRate: 1.1, swell: 0.12, rippleOnLevel: true, accent: false },
  thinking: { spin: 0.55, breatheAmp: 0.028, breatheRate: 2.4, swell: 0, rippleOnLevel: false, accent: true },
  speaking: { spin: 0.22, breatheAmp: 0.012, breatheRate: 1.6, swell: 0.14, rippleOnLevel: true, accent: true },
};

/** Unit vectors (x, y, z triples) spread evenly over a sphere; no clumping at the poles. */
export function fibonacciLattice(count: number): Float32Array {
  const out = new Float32Array(count * 3);
  for (let i = 0; i < count; i += 1) {
    const y = 1 - (i / Math.max(1, count - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = GOLDEN_ANGLE * i;
    out[i * 3] = Math.cos(theta) * r;
    out[i * 3 + 1] = y;
    out[i * 3 + 2] = Math.sin(theta) * r;
  }
  return out;
}

export interface Projected {
  /** Offset from the centre, in sphere radii after perspective. */
  x: number;
  y: number;
  /** 0 (far side) to 1 (near side). */
  depth: number;
}

/** Spins a lattice vector about the vertical axis by `yaw`, tilts it by `tilt`, projects it with perspective. */
export function project(x: number, y: number, z: number, yaw: number, tilt: number, out: Projected): Projected {
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  const x1 = x * cy + z * sy;
  const z1 = -x * sy + z * cy;
  const ct = Math.cos(tilt);
  const st = Math.sin(tilt);
  const y2 = y * ct - z1 * st;
  const z2 = y * st + z1 * ct;
  const scale = FOV / (FOV - z2);
  out.x = x1 * scale;
  out.y = y2 * scale;
  out.depth = clamp01((z2 + 1) / 2);
  return out;
}

export const depthAlpha = (depth: number): number => DEPTH_ALPHA_MIN + DEPTH_ALPHA_SPAN * clamp01(depth);
export const depthScale = (depth: number): number => DEPTH_SIZE_MIN + DEPTH_SIZE_SPAN * clamp01(depth);

/** Strength (0..1) of a ripple born `ageMs` ago at a dot `dist` radii from the centre of the disc. */
export function rippleWeight(dist: number, ageMs: number): number {
  if (ageMs < 0 || ageMs >= RIPPLE_MS) return 0;
  const life = ageMs / RIPPLE_MS;
  const ring = RIPPLE_REACH * smoothstep(life);
  const off = (dist - ring) / RIPPLE_WIDTH;
  return Math.exp(-off * off) * (1 - life);
}

/** One step of the level smoother. */
export function smoothLevel(current: number, target: number, dt: number): number {
  const rate = target > current ? LEVEL_ATTACK : LEVEL_RELEASE;
  return current + (clamp01(target) - current) * (1 - Math.exp(-rate * dt));
}

/** Finds syllable-like jumps of a level signal: the fast level far above the slow average, at most one per 160 ms. */
export class SpikeDetector {
  private slow = 0;
  private lastAt = -Infinity;

  /** `nowMs` is any monotonic clock. Returns true on a spike. */
  update(level: number, dt: number, nowMs: number): boolean {
    this.slow += (level - this.slow) * Math.min(1, dt * SLOW_RATE);
    if (level - this.slow <= SPIKE_MARGIN || nowMs - this.lastAt < RIPPLE_GAP_MS) return false;
    this.lastAt = nowMs;
    return true;
  }
}

/** Everything that decides whether the sphere still moves. */
export interface SphereMotion {
  mode: SphereMode;
  /** Smoothed level now and the level it is heading for. */
  level: number;
  target: number;
  /** Live ripples. */
  ripples: number;
  isStill: boolean;
}

const LEVEL_SETTLED = 0.002;

/**
 * Idle and thinking are still unless a ripple or a level is in flight: motion follows real events. Listening and
 * speaking are driven by the level, so they run for as long as the mode lasts.
 */
export function sphereNeedsFrame(m: SphereMotion): boolean {
  if (m.isStill) return false;
  if (m.ripples > 0) return true;
  if (m.mode === "listening" || m.mode === "speaking") return true;
  return Math.max(m.level, m.target) > LEVEL_SETTLED;
}

export interface SphereOptions {
  count?: number;
  /** Radius as a share of the shorter side of the canvas. */
  radiusShare?: number;
  random?: Random;
  colors?: readonly string[];
}

const DEFAULT_COUNT = 900;
const DEFAULT_RADIUS_SHARE = 0.36;

export class ParticleSphere {
  private readonly count: number;
  private readonly radiusShare: number;
  private readonly lattice: Float32Array;
  private readonly scatter: Float32Array;
  private readonly colors: readonly string[];
  private readonly host: CanvasHost;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly sx: Float32Array;
  private readonly sy: Float32Array;
  private readonly shade: Float32Array;
  private readonly pixel: Float32Array;
  private readonly tone: Uint8Array;
  private readonly ox: Float32Array;
  private readonly oy: Float32Array;
  private readonly size: Float32Array;
  private readonly muted: Uint8Array;
  private readonly spikes = new SpikeDetector();
  private readonly ripples: number[] = [];
  private readonly view: Projected = { x: 0, y: 0, depth: 0 };
  private readonly push: Vec2 = { x: 0, y: 0 };
  private mode: SphereMode = "idle";
  private target = 0;
  private level = 0;
  private morph = 1;
  private yaw = 0;
  private time = 0;
  private isDestroyed = false;
  private isStill = false;

  constructor(
    readonly canvas: HTMLCanvasElement,
    options: SphereOptions = {},
  ) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("2D canvas is not available");
    this.ctx = ctx;
    this.count = options.count ?? DEFAULT_COUNT;
    this.radiusShare = options.radiusShare ?? DEFAULT_RADIUS_SHARE;
    this.colors = options.colors ?? readToneColors();
    const random = options.random ?? seededRandom(2026);
    this.lattice = fibonacciLattice(this.count);
    this.scatter = new Float32Array(this.count * 2);
    this.muted = new Uint8Array(this.count);
    this.size = new Float32Array(this.count);
    for (let i = 0; i < this.count; i += 1) {
      this.scatter[i * 2] = (random() * 2 - 1) * 1.6;
      this.scatter[i * 2 + 1] = (random() * 2 - 1) * 1.6;
      this.muted[i] = random() < MUTED_SHARE ? 1 : 0;
      this.size[i] = DOT_SIZES[Math.floor(random() * DOT_SIZES.length)];
    }
    [this.sx, this.sy, this.shade, this.pixel, this.ox, this.oy] = [
      new Float32Array(this.count),
      new Float32Array(this.count),
      new Float32Array(this.count),
      new Float32Array(this.count),
      new Float32Array(this.count),
      new Float32Array(this.count),
    ];
    this.tone = new Uint8Array(this.count);
    this.host = createCanvasHost(canvas, (dt) => this.frame(dt));
  }

  setMode(mode: SphereMode): void {
    this.mode = mode;
    this.host.wake();
  }

  /** 0..1, for example a microphone or playback level. Smoothed here. */
  setLevel(level: number): void {
    this.target = clamp01(level);
    this.host.wake();
  }

  /** 0 = dots scattered, 1 = a sphere. For an animation that gathers the dots. */
  setMorph(morph: number): void {
    this.morph = clamp01(morph);
    this.host.wake();
  }

  /** Sends one ripple out from the centre now. */
  pulse(): void {
    if (this.ripples.length >= RIPPLES_MAX) this.ripples.shift();
    this.ripples.push(this.time);
    this.host.wake();
  }

  /** Draws one frame and nothing else (effects off or reduced motion). */
  drawStill(): void {
    this.isStill = true;
    this.host.frameOnce();
  }

  /** Back to the animated loop after `drawStill`. */
  play(): void {
    this.isStill = false;
    this.host.wake();
  }

  /**
   * Where the dots are on screen now, as x, y pairs in viewport px. Pass them to `ParticleLayer.assemble` as
   * `from` positions (or `seed`) to hand the sphere's dots to a layer.
   */
  exportPoints(): Points {
    const rect = this.canvas.getBoundingClientRect();
    const out = new Float32Array(this.count * 2);
    for (let i = 0; i < this.count; i += 1) {
      out[i * 2] = rect.left + this.sx[i];
      out[i * 2 + 1] = rect.top + this.sy[i];
    }
    return out;
  }

  destroy(): void {
    if (this.isDestroyed) return;
    this.isDestroyed = true;
    this.host.dispose();
    this.ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  // ── Frame ──────────────────────────────────────────────────────────────────────────────────────────────────────

  private frame(dt: number): boolean {
    if (this.isDestroyed) return false;
    const look = MODE_LOOK[this.mode];
    this.time += dt * 1000;
    this.level = smoothLevel(this.level, this.target, dt);
    this.yaw += look.spin * dt;
    this.stepRipples(look, dt);
    this.layout(look, dt);
    this.draw();
    return sphereNeedsFrame(this.motion());
  }

  private motion(): SphereMotion {
    return { mode: this.mode, level: this.level, target: this.target, ripples: this.ripples.length, isStill: this.isStill };
  }

  private stepRipples(look: ModeLook, dt: number): void {
    while (this.ripples.length && this.time - this.ripples[0] >= RIPPLE_MS) this.ripples.shift();
    if (look.rippleOnLevel && this.spikes.update(this.level, dt, this.time)) this.pulse();
  }

  /** Projects every dot to canvas px, applies the pointer and picks its tone, size and alpha. */
  private layout(look: ModeLook, dt: number): void {
    const { w, h } = this.host.size;
    const { pointer } = this.host;
    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w, h) * this.radiusShare * this.currentScale(look);
    const rect = this.canvas.getBoundingClientRect();
    const px = pointer.x - rect.left;
    const py = pointer.y - rect.top;
    const gather = smoothstep(this.morph);
    decayPointer(pointer, dt);
    for (let i = 0; i < this.count; i += 1) {
      const p = project(this.lattice[i * 3], this.lattice[i * 3 + 1], this.lattice[i * 3 + 2], this.yaw, TILT, this.view);
      const gx = cx + this.scatter[i * 2] * radius;
      const gy = cy + this.scatter[i * 2 + 1] * radius;
      this.sx[i] = gx + (cx + p.x * radius - gx) * gather;
      this.sy[i] = gy + (cy + p.y * radius - gy) * gather;
      this.applyPointer(i, px, py, pointer.on, pointer.vx, pointer.vy, dt, radius);
      const ripple = this.rippleAt(Math.hypot(p.x, p.y));
      this.shade[i] = Math.min(1, depthAlpha(p.depth) + ripple * 0.5) * (0.3 + 0.7 * gather);
      this.pixel[i] = Math.min(DOT_MAX_PX, this.size[i] * depthScale(p.depth) * (1 + ripple * 0.35));
      this.tone[i] = ripple > RIPPLE_TONE_AT && look.accent ? TONE.accent : this.muted[i] ? TONE.muted : TONE.ink;
    }
  }

  private currentScale(look: ModeLook): number {
    const breathe = 1 + Math.sin((this.time / 1000) * look.breatheRate) * look.breatheAmp;
    return breathe * (1 + this.level * look.swell);
  }

  private rippleAt(dist: number): number {
    let strongest = 0;
    for (const born of this.ripples) strongest = Math.max(strongest, rippleWeight(dist, this.time - born));
    return strongest;
  }

  private applyPointer(i: number, px: number, py: number, on: boolean, vx: number, vy: number, dt: number, radius: number): void {
    if (on) repelForce(this.push, this.sx[i] - px, this.sy[i] - py, vx, vy);
    this.ox[i] = approach(this.ox[i], on ? this.push.x * Math.min(1, radius / 80) : 0, dt);
    this.oy[i] = approach(this.oy[i], on ? this.push.y * Math.min(1, radius / 80) : 0, dt);
    this.sx[i] += this.ox[i];
    this.sy[i] += this.oy[i];
  }

  private draw(): void {
    const { ctx } = this;
    const { w, h, dpr } = this.host.size;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    for (let tone = 0; tone < this.colors.length; tone += 1) {
      ctx.fillStyle = this.colors[tone];
      for (let i = 0; i < this.count; i += 1) {
        if (this.tone[i] !== tone) continue;
        const s = this.pixel[i];
        ctx.globalAlpha = this.shade[i];
        ctx.fillRect(Math.round((this.sx[i] - s / 2) * dpr) / dpr, Math.round((this.sy[i] - s / 2) * dpr) / dpr, s, s);
      }
    }
    ctx.globalAlpha = 1;
  }
}
