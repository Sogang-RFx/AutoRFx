/**
 * Dots streaming through an ordered list of gates (docs/design/immersive-ui.md §4.3). Pure typed-array logic.
 *
 * A lane is a list of gates, each with a position and a state that comes from real data (a job's stages, a graph
 * edge). What the dots do follows the states and nothing else:
 *   done     dots rest at the gate (a small cloud);
 *   error    dots rest at the gate, drawn in the danger tone;
 *   active   dots stream from the gate to the next gate that is not skipped, again and again;
 *   skipped  dots pass through without stopping;
 *   pending  nothing reaches it.
 * With no done, error or active gate, the dots wait at the first gate: motion is never made up.
 */
import { clamp01, smoothstep } from "./motion";
import { MUTED_SHARE, TONE, type Tone } from "./tone";

export type GateState = "pending" | "active" | "done" | "error" | "skipped";

export interface FlowGate {
  /** Canvas px of the layer the flow runs on. */
  x: number;
  y: number;
  state: GateState;
}

export interface FlowLane {
  gates: readonly FlowGate[];
  /** Share of the dots on this lane (default 1). */
  weight?: number;
}

export const BUCKET = { wait: 0, rest: 1, stream: 2 } as const;
type BucketKind = (typeof BUCKET)[keyof typeof BUCKET];

/** Where a share of the dots goes. `from` and `to` are gate indexes (floats along the lane). */
export interface Bucket {
  kind: BucketKind;
  from: number;
  to: number;
  danger: boolean;
}

const WAIT_BUCKET: Bucket = { kind: BUCKET.wait, from: 0, to: 0, danger: false };

/** The end of the stream that starts at gate `index`: the next gate that is not skipped, else the last one. */
export function streamEnd(gates: readonly FlowGate[], index: number): number {
  for (let j = index + 1; j < gates.length; j += 1) if (gates[j].state !== "skipped") return j;
  return gates.length - 1;
}

/** One bucket per gate that holds or emits dots, in lane order. Never empty. */
export function planBuckets(gates: readonly FlowGate[]): Bucket[] {
  const buckets: Bucket[] = [];
  gates.forEach((gate, i) => {
    if (gate.state === "done" || gate.state === "error") {
      buckets.push({ kind: BUCKET.rest, from: i, to: i, danger: gate.state === "error" });
    } else if (gate.state === "active") {
      const to = streamEnd(gates, i);
      buckets.push(to > i ? { kind: BUCKET.stream, from: i, to, danger: false } : { kind: BUCKET.rest, from: i, to: i, danger: false });
    }
  });
  return buckets.length ? buckets : [WAIT_BUCKET];
}

/**
 * Is anything in transit: does any lane send dots from one gate to another? Only an active gate with somewhere to
 * stream to does. With none, dots settle on their gates and the layer may stop its loop.
 */
export function isInTransit(lanes: readonly FlowLane[]): boolean {
  return lanes.some((lane) => planBuckets(lane.gates).some((bucket) => bucket.kind === BUCKET.stream));
}

/** `rank` (0..1, fixed per dot) picks a bucket; equal shares. */
export function pickBucket(buckets: readonly Bucket[], rank: number): Bucket {
  return buckets[Math.min(buckets.length - 1, Math.floor(clamp01(rank) * buckets.length))];
}

/** Moves a dot's place `s` along the lane by `speed * dt` according to its bucket. */
export function stepAlong(s: number, bucket: Bucket, speed: number, dt: number): number {
  const move = speed * dt;
  if (bucket.kind === BUCKET.stream) {
    const next = s + move;
    return next >= bucket.to ? bucket.from : next;
  }
  if (s < bucket.to) return Math.min(bucket.to, s + move);
  // A dot beyond its gate (the lane changed) comes back faster than it went.
  return Math.max(bucket.to, s - move * BACK_FACTOR);
}

const BACK_FACTOR = 2;

export interface LanePoint {
  x: number;
  y: number;
  /** Unit vector across the lane, for sideways spread. */
  nx: number;
  ny: number;
}

/** Position at place `s` (0 = first gate, 1 = second …) on the polyline through the gates. */
export function locate(gates: readonly FlowGate[], s: number, out: LanePoint): LanePoint {
  const last = gates.length - 1;
  const i0 = Math.max(0, Math.min(last, Math.floor(s)));
  const i1 = Math.min(last, i0 + 1);
  const a = gates[i0];
  const b = gates[i1];
  const f = i1 === i0 ? 0 : clamp01(s - i0);
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  out.x = a.x + dx * f;
  out.y = a.y + dy * f;
  out.nx = -dy / len;
  out.ny = dx / len;
  return out;
}

// ── Dot population ───────────────────────────────────────────────────────────────────────────────────────────────

/** Where a flow dot is drawn, written by `FlowField.advance`. */
export interface FlowOut {
  x: number;
  y: number;
  /** Alpha share 0..1. */
  a: number;
  tone: Tone;
}

export interface FlowOptions {
  /** Gate-spans per second for an average dot. */
  speed?: number;
  /** Sideways spread of a stream, px. */
  spread?: number;
  /** Radius of the cloud at a resting gate, px. */
  restRadius?: number;
}

const DEFAULT_SPEED = 0.8;
const DEFAULT_SPREAD = 8;
const DEFAULT_REST_RADIUS = 10;
const SPEED_SPAN = 0.7;
const SPEED_FLOOR = 0.6;
const ACCENT_SHARE = 0.3;
/** Dots are lighter while they wait, and fade in and out at the ends of a stream. */
const WAIT_ALPHA = 0.45;
const STREAM_EDGE = 0.12;
/** Within this many gate-spans of its gate, a resting dot is blended into its cloud. */
const SETTLE_SPAN = 0.25;

export class FlowField {
  private readonly s: Float32Array;
  private readonly speed: Float32Array;
  private readonly side: Float32Array;
  private readonly jx: Float32Array;
  private readonly jy: Float32Array;
  private readonly rank: Float32Array;
  private readonly pick: Float32Array;
  private readonly accent: Uint8Array;
  private readonly base: Uint8Array;
  private readonly lane: Int16Array;
  private readonly bucket: Bucket[];
  private lanes: readonly FlowLane[] = [];
  private buckets: Bucket[][] = [];
  private hasPlan = false;
  private moved = false;
  private readonly point: LanePoint = { x: 0, y: 0, nx: 0, ny: 0 };

  constructor(
    readonly count: number,
    private readonly random: () => number,
    private readonly options: FlowOptions = {},
  ) {
    this.s = new Float32Array(count);
    this.speed = new Float32Array(count);
    this.side = new Float32Array(count);
    this.jx = new Float32Array(count);
    this.jy = new Float32Array(count);
    this.rank = new Float32Array(count);
    this.pick = new Float32Array(count);
    this.accent = new Uint8Array(count);
    this.base = new Uint8Array(count);
    this.lane = new Int16Array(count);
    this.bucket = new Array<Bucket>(count).fill(WAIT_BUCKET);
    const radius = options.restRadius ?? DEFAULT_REST_RADIUS;
    for (let i = 0; i < count; i += 1) {
      this.speed[i] = (SPEED_FLOOR + random() * SPEED_SPAN) * (options.speed ?? DEFAULT_SPEED);
      this.side[i] = random() * 2 - 1;
      const angle = random() * Math.PI * 2;
      const r = radius * Math.sqrt(random());
      this.jx[i] = Math.cos(angle) * r;
      this.jy[i] = Math.sin(angle) * r;
      this.rank[i] = random();
      this.pick[i] = random();
      this.accent[i] = random() < ACCENT_SHARE ? 1 : 0;
      this.base[i] = random() < MUTED_SHARE ? TONE.muted : TONE.ink;
    }
  }

  /** Replaces the lanes (positions and states). Dots keep their place and move toward their new bucket. */
  setLanes(lanes: readonly FlowLane[]): void {
    this.lanes = lanes;
    this.buckets = lanes.map((lane) => planBuckets(lane.gates));
    const weights = lanes.map((lane) => (lane.gates.length ? Math.max(0, lane.weight ?? 1) : 0));
    const total = weights.reduce((sum, w) => sum + w, 0);
    for (let i = 0; i < this.count; i += 1) {
      const laneIndex = total > 0 ? this.laneFor(this.pick[i] * total, weights) : -1;
      this.lane[i] = laneIndex;
      if (laneIndex < 0) continue;
      const bucket = pickBucket(this.buckets[laneIndex], this.rank[i]);
      this.bucket[i] = bucket;
      // The first plan scatters stream dots along their stream so they do not start in a clump.
      if (!this.hasPlan && bucket.kind === BUCKET.stream) this.s[i] = bucket.from + this.random() * (bucket.to - bucket.from);
    }
    this.hasPlan = true;
  }

  private laneFor(target: number, weights: readonly number[]): number {
    let acc = 0;
    for (let k = 0; k < weights.length; k += 1) {
      acc += weights[k];
      if (target < acc && weights[k] > 0) return k;
    }
    return weights.findLastIndex((w) => w > 0);
  }

  /** Call before a frame's dots are advanced. */
  startFrame(): void {
    this.moved = false;
  }

  /** True when a dot of the current frame streamed or changed its place: false means everything has settled. */
  get isMoving(): boolean {
    return this.moved;
  }

  /** The bucket of dot `i` (for tests and for callers that mirror the state). */
  bucketOf(i: number): Bucket {
    return this.bucket[i];
  }

  placeOf(i: number): number {
    return this.s[i];
  }

  /** Moves dot `i` by `dt` seconds and writes where and how to draw it. `out.a` is 0 for a dot with no lane. */
  advance(i: number, dt: number, out: FlowOut): FlowOut {
    const laneIndex = this.lane[i];
    if (laneIndex < 0) {
      out.a = 0;
      return out;
    }
    const gates = this.lanes[laneIndex].gates;
    const bucket = this.bucket[i];
    const before = this.s[i];
    this.s[i] = stepAlong(before, bucket, this.speed[i], dt);
    const s = this.s[i];
    if (s !== before || bucket.kind === BUCKET.stream) this.moved = true;
    const spot = locate(gates, s, this.point);
    const spread = this.options.spread ?? DEFAULT_SPREAD;
    const settle = bucket.kind === BUCKET.stream ? 1 : smoothstep(Math.abs(bucket.to - s) / SETTLE_SPAN);
    out.x = this.point.x + (this.point.nx * this.side[i] * spread) * settle + this.jx[i] * (1 - settle);
    out.y = spot.y + (this.point.ny * this.side[i] * spread) * settle + this.jy[i] * (1 - settle);
    out.a = this.alphaFor(bucket, s);
    out.tone = this.toneFor(i, bucket);
    return out;
  }

  private alphaFor(bucket: Bucket, s: number): number {
    if (bucket.kind === BUCKET.wait) return WAIT_ALPHA;
    if (bucket.kind === BUCKET.rest) return 1;
    const u = (s - bucket.from) / Math.max(1e-6, bucket.to - bucket.from);
    return smoothstep(u / STREAM_EDGE) * smoothstep((1 - u) / STREAM_EDGE);
  }

  private toneFor(i: number, bucket: Bucket): Tone {
    if (bucket.danger) return TONE.danger;
    if (bucket.kind === BUCKET.stream && this.accent[i]) return TONE.accent;
    return this.base[i] as Tone;
  }
}
