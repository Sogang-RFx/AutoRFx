/**
 * Motion maths of the particle layer. Pure functions, no DOM: unit-tested in node.
 * The millisecond values mirror the `--dur-fx-*` tokens in globals.css (docs/design/immersive-ui.md §2);
 * a test keeps the two in step.
 */

export const FLIGHT_MIN_MS = 520;
export const FLIGHT_MAX_MS = 900;
export const FLIGHT_JITTER_MS = 180;
/** Real text fades in over this long while the dots fade out. */
export const REVEAL_FADE_MS = 460;
export const COVER_FADE_MS = 480;
/** The real element starts to show once the flight has covered this share of its time. */
export const HANDOFF_AT = 0.78;

/** Sideways bend of a path, as a share of its length (per dot, signed). */
export const BEND_MAX = 0.28;
export const SWAY_PX = 12;
const SWAY_RATE_X = 5.1;
const SWAY_RATE_Y = 3.7;
const SWAY_PHASE_Y = 1.3;
/** Fraction of the way along the chord for the two inner Bezier handles. */
const HANDLE_NEAR = 0.3;
const HANDLE_FAR = 0.72;
const HANDLE_FAR_BEND = 0.45;
/** The sway grows over the first quarter of the flight. */
const SWAY_RAMP = 4;

export const REPEL_SIGMA_PX = 45;
export const REPEL_CUTOFF_SIGMAS = 3;
export const REPEL_PUSH_PX = 24;
/** How much of the pointer's own speed drags a dot along. */
export const REPEL_VELOCITY_GAIN = 0.035;
/** Per second: how fast a pushed dot returns. */
export const SPRING_RATE = 12;
/** Per second: how fast the remembered pointer speed dies out. */
export const POINTER_DECAY = 5;

const REPEL_REACH_SQ = (REPEL_SIGMA_PX * REPEL_CUTOFF_SIGMAS) ** 2;
const TWO_SIGMA_SQ = 2 * REPEL_SIGMA_PX * REPEL_SIGMA_PX;

export interface Vec2 {
  x: number;
  y: number;
}

export const clamp01 = (t: number): number => (t < 0 ? 0 : t > 1 ? 1 : t);

export function smoothstep(t: number): number {
  const c = clamp01(t);
  return c * c * (3 - 2 * c);
}

/** Mean of smoothstep and ease-out cubic: a soft start and a long landing. Monotonic, 0 at 0 and 1 at 1. */
export function easeFlight(t: number): number {
  const c = clamp01(t);
  return (smoothstep(c) + 1 - (1 - c) ** 3) / 2;
}

/**
 * Position of a dot `t` (0..1) of the way through its flight, written into `out`.
 * A cubic Bezier whose inner handles are pushed sideways by `bend`, plus a sway that dies out on landing.
 * At t <= 0 the result is exactly the start and at t >= 1 exactly the target.
 */
export function flightPoint(
  out: Vec2,
  t: number,
  sx: number,
  sy: number,
  tx: number,
  ty: number,
  bend: number,
  seconds: number,
  phase: number,
): Vec2 {
  if (t <= 0) {
    out.x = sx;
    out.y = sy;
    return out;
  }
  if (t >= 1) {
    out.x = tx;
    out.y = ty;
    return out;
  }
  const e = easeFlight(t);
  const m = 1 - e;
  const dx = tx - sx;
  const dy = ty - sy;
  const sway = SWAY_PX * m * smoothstep(t * SWAY_RAMP);
  const a = 3 * m * m * e;
  const b = 3 * m * e * e;
  // The sideways direction of a chord (dx, dy) is (-dy, dx).
  out.x =
    m * m * m * sx + a * (sx + dx * HANDLE_NEAR - dy * bend) + b * (sx + dx * HANDLE_FAR - dy * bend * HANDLE_FAR_BEND) + e * e * e * tx +
    Math.sin(seconds * SWAY_RATE_X + phase) * sway;
  out.y =
    m * m * m * sy + a * (sy + dy * HANDLE_NEAR + dx * bend) + b * (sy + dy * HANDLE_FAR + dx * bend * HANDLE_FAR_BEND) + e * e * e * ty +
    Math.cos(seconds * SWAY_RATE_Y + phase * SWAY_PHASE_Y) * sway;
  return out;
}

/**
 * Push on a dot at offset (dx, dy) from the pointer: Gaussian fall-off, away from the pointer, plus a drag with the
 * pointer's own velocity (vx, vy). Exactly zero beyond REPEL_CUTOFF_SIGMAS sigmas.
 */
export function repelForce(out: Vec2, dx: number, dy: number, vx: number, vy: number): Vec2 {
  const d2 = dx * dx + dy * dy;
  if (d2 >= REPEL_REACH_SQ) {
    out.x = 0;
    out.y = 0;
    return out;
  }
  const g = Math.exp(-d2 / TWO_SIGMA_SQ);
  const d = Math.sqrt(d2);
  // On the pointer itself there is no direction: push straight up.
  const ux = d > 0 ? dx / d : 0;
  const uy = d > 0 ? dy / d : -1;
  out.x = (ux * REPEL_PUSH_PX + vx * REPEL_VELOCITY_GAIN) * g;
  out.y = (uy * REPEL_PUSH_PX + vy * REPEL_VELOCITY_GAIN) * g;
  return out;
}

/** One step of a spring-back toward `target`: frame-rate independent enough at the 50 ms frame cap. */
export function approach(current: number, target: number, dt: number, rate: number = SPRING_RATE): number {
  return current + (target - current) * Math.min(1, dt * rate);
}

/** Small seeded generator (mulberry32) for tests and for repeatable scatter. Returns numbers in [0, 1). */
export function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
