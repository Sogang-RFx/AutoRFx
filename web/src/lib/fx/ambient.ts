/**
 * The background dot field of every screen (docs/design/immersive-ui.md §4.6). Pure maths on typed arrays: no DOM,
 * no React. Dots drift along a slow flow field, yield to the pointer and spring back, and wrap around the edges.
 * The dots here show nothing about progress: they are only the texture of the page.
 */
import { approach, repelForce, seededRandom, type Vec2 } from "./motion";
import { MUTED_SHARE, TONE } from "./tone";

/** One dot per this much screen area. */
export const DOT_AREA_PX2 = 700;
export const MAX_DOTS = 3200;

export const SPEED_MIN_PX = 10;
export const SPEED_MAX_PX = 26;

export const ALPHA_MIN = 0.25;
export const ALPHA_MAX = 0.7;
/** Opacity comes in a few steps so a frame can draw all dots of one step with one fill style. */
export const ALPHA_LEVELS = 4;
/** Dot edge lengths in CSS px: crisp squares. */
export const DOT_SIZES_PX = [1, 1.5, 2] as const;
/** A dot may leave the screen by this much before it re-enters on the other side, so it never pops at the border. */
export const WRAP_MARGIN_PX = 8;

// Flow field: three slow waves added into one angle. Wavelengths are about 1,200 to 2,000 px and a full change of
// pattern takes minutes, so neighbours move alike and the picture breathes instead of churning.
const WAVE_X = 0.0042;
const WAVE_Y = 0.0051;
const WAVE_XY = 0.003;
const RATE_X = 0.07;
const RATE_Y = 0.05;
const RATE_XY = 0.03;
const PHASE_Y = 1.7;
const PHASE_XY = 0.6;

export function dotCountFor(width: number, height: number): number {
  if (!(width > 0) || !(height > 0)) return 0;
  return Math.min(MAX_DOTS, Math.max(1, Math.round((width * height) / DOT_AREA_PX2)));
}

/** Direction of the flow at (x, y) and time t (seconds), in radians. Smooth in space and slow in time. */
export function flowAngle(x: number, y: number, t: number): number {
  return Math.PI * (Math.sin(x * WAVE_X + t * RATE_X) + Math.cos(y * WAVE_Y - t * RATE_Y + PHASE_Y) + Math.sin((x + y) * WAVE_XY + t * RATE_XY + PHASE_XY));
}

/** The same direction as a unit vector, written into `out`. */
export function flowDirection(out: Vec2, x: number, y: number, t: number): Vec2 {
  const a = flowAngle(x, y, t);
  out.x = Math.cos(a);
  out.y = Math.sin(a);
  return out;
}

/** Brings a coordinate into [-margin, size + margin) by wrapping, however far outside it is (after a resize). */
export function wrapCoord(v: number, size: number): number {
  const span = size + 2 * WRAP_MARGIN_PX;
  return ((((v + WRAP_MARGIN_PX) % span) + span) % span) - WRAP_MARGIN_PX;
}

export const levelAlpha = (level: number): number => ALPHA_MIN + ((ALPHA_MAX - ALPHA_MIN) * level) / (ALPHA_LEVELS - 1);

/** Dots as parallel typed arrays. `x, y` is where the flow has carried a dot, `ox, oy` how far the pointer pushed it. */
export interface AmbientDots {
  count: number;
  x: Float32Array;
  y: Float32Array;
  ox: Float32Array;
  oy: Float32Array;
  speed: Float32Array;
  tone: Uint8Array;
  /** Index into DOT_SIZES_PX. */
  size: Uint8Array;
  /** Opacity step, 0..ALPHA_LEVELS-1. */
  level: Uint8Array;
}

export function createDots(capacity: number = MAX_DOTS): AmbientDots {
  return {
    count: 0,
    x: new Float32Array(capacity),
    y: new Float32Array(capacity),
    ox: new Float32Array(capacity),
    oy: new Float32Array(capacity),
    speed: new Float32Array(capacity),
    tone: new Uint8Array(capacity),
    size: new Uint8Array(capacity),
    level: new Uint8Array(capacity),
  };
}

function spawnDot(d: AmbientDots, i: number, width: number, height: number, random: () => number): void {
  d.x[i] = random() * width;
  d.y[i] = random() * height;
  d.ox[i] = 0;
  d.oy[i] = 0;
  d.speed[i] = SPEED_MIN_PX + random() * (SPEED_MAX_PX - SPEED_MIN_PX);
  d.tone[i] = random() < MUTED_SHARE ? TONE.muted : TONE.ink;
  d.size[i] = Math.min(DOT_SIZES_PX.length - 1, Math.floor(random() * DOT_SIZES_PX.length));
  d.level[i] = Math.min(ALPHA_LEVELS - 1, Math.floor(random() * ALPHA_LEVELS));
}

/** Makes the count follow the screen area: the dots that exist stay where they are, only the difference is added or dropped. */
export function resizeDots(d: AmbientDots, width: number, height: number, random: () => number = seededRandom(1)): void {
  const next = Math.min(d.x.length, dotCountFor(width, height));
  for (let i = d.count; i < next; i += 1) spawnDot(d, i, width, height, random);
  d.count = next;
}

/** What the pointer gives the field: viewport px and px per second. */
export interface PushSource {
  x: number;
  y: number;
  vx: number;
  vy: number;
  on: boolean;
}

const DIR: Vec2 = { x: 0, y: 0 };
const PUSH: Vec2 = { x: 0, y: 0 };

/** One step of dot `i` over `dt` seconds at flow time `t`, on a screen of width x height px. */
export function stepDot(d: AmbientDots, i: number, dt: number, t: number, width: number, height: number, pointer: PushSource): void {
  flowDirection(DIR, d.x[i], d.y[i], t);
  const travel = d.speed[i] * dt;
  d.x[i] = wrapCoord(d.x[i] + DIR.x * travel, width);
  d.y[i] = wrapCoord(d.y[i] + DIR.y * travel, height);
  let pushX = 0;
  let pushY = 0;
  if (pointer.on) {
    repelForce(PUSH, d.x[i] - pointer.x, d.y[i] - pointer.y, pointer.vx, pointer.vy);
    pushX = PUSH.x;
    pushY = PUSH.y;
  }
  // With no push in reach the target is zero, which is the spring-back.
  d.ox[i] = approach(d.ox[i], pushX, dt);
  d.oy[i] = approach(d.oy[i], pushY, dt);
}

export type AmbientFrameMode = "none" | "still" | "run";

export interface AmbientFrameInput {
  /** The "화면 효과" switch after `?fx=0`. */
  enabled: boolean;
  reduced: boolean;
  /** The tab is not visible. */
  hidden: boolean;
  dots: number;
  /** The still picture for the current canvas size is already on the canvas. */
  stillDrawn: boolean;
}

/** `run`: keep a frame loop. `still`: draw one frame and stop. `none`: draw nothing. */
export function ambientFrameMode(input: AmbientFrameInput): AmbientFrameMode {
  if (!input.enabled || input.hidden || input.dots <= 0) return "none";
  if (!input.reduced) return "run";
  return input.stillDrawn ? "none" : "still";
}
