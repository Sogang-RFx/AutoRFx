/**
 * Pure decisions of the intro (docs/design/immersive-ui.md §4.1): whether it plays, when each phase starts,
 * where the name goes, and the guards. No DOM, no React: unit-tested in node.
 */
import { COVER_FADE_MS, FLIGHT_JITTER_MS, HANDOFF_AT, REVEAL_FADE_MS } from "@/lib/fx/motion";
import type { StorageLike } from "@/lib/fx/prefs";

export const INTRO_SEEN_KEY = "autorfx.intro";
export const INTRO_QUERY_PARAM = "intro";
export const WORDMARK = "AutoRFx";
export const TAGLINE = "RFQ를 조항으로 나누고, 모든 판단을 원문으로 되짚습니다.";

export const INTRO_TIMING = {
  /** Sphere phase: dots gather, then the name takes over. */
  sphereMs: 1050,
  /** The gathering itself is shorter than the phase, so the sphere also turns for a moment. */
  morphMs: 900,
  pulseAtMs: 480,
  nameFlightMs: 700,
  nameJitterMs: FLIGHT_JITTER_MS,
  taglineDelayMs: 55,
  holdMs: 360,
  handoffMs: 520,
  handoffJitterMs: 120,
  handoffStaggerMs: 30,
  /** The screen underneath may still be loading (auth check): the name is held at most this long. */
  screenWaitMs: 2500,
  /** The screen is taken as settled once nothing changed in the document for this long. */
  screenQuietMs: 200,
  fontWaitMs: 1200,
  /** Whatever happens, the intro is over by now. */
  hardCapMs: 12000,
} as const;

// ── Whether to play ──────────────────────────────────────────────────────────────────────────────────────────────

export interface IntroInputs {
  /** The page's query string, e.g. "?intro=1". */
  search: string;
  /** Already played in this browser session. */
  seen: boolean;
  /** `fxMoves()`: effects on and no reduced motion. */
  moves: boolean;
  /** Asked for from the account menu. */
  replay?: boolean;
}

export function shouldPlayIntro({ search, seen, moves, replay = false }: IntroInputs): boolean {
  if (!moves) return false;
  if (replay) return true;
  const flag = new URLSearchParams(search).get(INTRO_QUERY_PARAM);
  if (flag === "0") return false;
  if (flag === "1") return true;
  return !seen;
}

/** Storage may be missing or throw (blocked cookies): then the intro is simply "not seen". */
export function readIntroSeen(storage: () => StorageLike | null): boolean {
  try {
    return storage()?.getItem(INTRO_SEEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function markIntroSeen(storage: () => StorageLike | null): void {
  try {
    storage()?.setItem(INTRO_SEEN_KEY, "1");
  } catch {
    // Blocked storage: the intro plays again on the next load, which is harmless.
  }
}

// ── Timeline ─────────────────────────────────────────────────────────────────────────────────────────────────────

export interface IntroTimeline {
  /** The sphere's dots start to fly to the name. */
  nameAt: number;
  nameLandedAt: number;
  /** The dots leave the name for the real screen (the cover starts to lift). */
  handoffAt: number;
  /** The real elements start to fade in. */
  showAt: number;
  /** Everything is over: dots faded, elements shown. */
  endAt: number;
}

/** Milliseconds from the start. `screenWaitMs` is how long the screen underneath kept the hand-off waiting. */
export function introTimeline(elementCount: number, screenWaitMs = 0): IntroTimeline {
  const t = INTRO_TIMING;
  const nameAt = t.sphereMs;
  const nameLandedAt = nameAt + t.taglineDelayMs + t.nameFlightMs + t.nameJitterMs;
  const handoffAt = nameLandedAt + t.holdMs + Math.max(0, screenWaitMs);
  const reach = handoffReach(elementCount);
  return {
    nameAt,
    nameLandedAt,
    handoffAt,
    showAt: handoffAt + reach * HANDOFF_AT,
    endAt: Math.max(handoffAt + reach + REVEAL_FADE_MS, handoffAt + COVER_FADE_MS),
  };
}

/** Time from the first dot leaving to the last one landing. */
export function handoffReach(elementCount: number): number {
  const t = INTRO_TIMING;
  return Math.max(0, elementCount - 1) * t.handoffStaggerMs + t.handoffJitterMs + t.handoffMs;
}

// ── The name on screen ───────────────────────────────────────────────────────────────────────────────────────────

export interface NameLayout {
  cx: number;
  wordY: number;
  wordSize: number;
  /** Letter spacing of the wordmark, px (negative: tighter). */
  wordSpacingPx: number;
  tagSize: number;
  tagLines: Array<{ text: string; y: number }>;
}

const WORD_MIN_PX = 44;
const WORD_MAX_PX = 128;
const WORD_WIDTH_SHARE = 0.1;
const WORD_HEIGHT_SHARE = 0.18;
const WORD_SPACING_EM = -0.02;
const TAG_MIN_PX = 12;
const TAG_MAX_PX = 22;
const TAG_BASE_PX = 8;
const TAG_WIDTH_SHARE = 0.016;
const TAG_LINE_HEIGHT = 1.5;
const TAG_MAX_WIDTH_SHARE = 0.86;
const BLOCK_LIFT_SHARE = 0.04;

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

/** Rough width of a line: Hangul is about one em wide, Latin about half, spaces and commas narrow. */
export function estimateWidth(text: string, size: number): number {
  let em = 0;
  for (const ch of text) {
    if (ch === " ") em += 0.28;
    else if (ch === "," || ch === ".") em += 0.35;
    else if (ch.charCodeAt(0) > 0x2e80) em += 1;
    else em += 0.6;
  }
  return em * size;
}

/** One line when it fits, else two, broken after the comma (or at the space) nearest the middle. */
export function taglineLines(text: string, size: number, maxWidth: number): string[] {
  if (estimateWidth(text, size) <= maxWidth) return [text];
  const middle = text.length / 2;
  const nearest = (indexes: number[]): number =>
    indexes.reduce((best, i) => (Math.abs(i - middle) < Math.abs(best - middle) ? i : best), indexes[0]);
  const commas = [...text.matchAll(/,\s/g)].map((m) => (m.index ?? 0) + 1);
  const spaces = [...text.matchAll(/\s/g)].map((m) => m.index ?? 0);
  const cuts = commas.length ? commas : spaces;
  if (!cuts.length) return [text];
  const cut = nearest(cuts);
  return [text.slice(0, cut).trim(), text.slice(cut).trim()];
}

export function nameLayout(width: number, height: number): NameLayout {
  const wordSize = clamp(Math.min(width * WORD_WIDTH_SHARE, height * WORD_HEIGHT_SHARE), WORD_MIN_PX, WORD_MAX_PX);
  const maxWidth = width * TAG_MAX_WIDTH_SHARE;
  let tagSize = clamp(TAG_BASE_PX + width * TAG_WIDTH_SHARE, TAG_MIN_PX + 2, TAG_MAX_PX);
  let lines = taglineLines(TAGLINE, tagSize, maxWidth);
  const widest = Math.max(...lines.map((line) => estimateWidth(line, tagSize)));
  // Still too wide after the split: shrink to fit, down to a readable floor.
  if (widest > maxWidth) {
    tagSize = Math.max(TAG_MIN_PX, (tagSize * maxWidth) / widest);
    lines = taglineLines(TAGLINE, tagSize, maxWidth);
  }
  const gap = wordSize * 0.25;
  const lineHeight = tagSize * TAG_LINE_HEIGHT;
  const blockHeight = wordSize * 1.1 + gap + lines.length * lineHeight;
  const top = (height - blockHeight) / 2 - height * BLOCK_LIFT_SHARE;
  return {
    cx: width / 2,
    wordY: top + wordSize * 0.55,
    wordSize,
    wordSpacingPx: wordSize * WORD_SPACING_EM,
    tagSize,
    tagLines: lines.map((text, i) => ({ text, y: top + wordSize * 1.1 + gap + lineHeight * (i + 0.5) })),
  };
}

// ── Guards ───────────────────────────────────────────────────────────────────────────────────────────────────────

export interface ViewSize {
  w: number;
  h: number;
}

/** Height changes this small are a mobile address bar sliding, not a resize. */
const RESIZE_HEIGHT_TOLERANCE_PX = 120;

/** A resize that moves the layout the dots were sampled for: the intro ends instead of leaving dots misplaced. */
export function resizeEndsIntro(from: ViewSize, to: ViewSize): boolean {
  return from.w !== to.w || Math.abs(from.h - to.h) > RESIZE_HEIGHT_TOLERANCE_PX;
}

export interface IntroBudget {
  sphere: number;
  name: number;
  handoff: number;
}

const SMALL_VIEW_PX = 640;
const FEW_CORES = 4;
const FULL_BUDGET: IntroBudget = { sphere: 1650, name: 4800, handoff: 4200 };
const LIGHT_BUDGET: IntroBudget = { sphere: 900, name: 2400, handoff: 2400 };

/** Fewer dots on a phone or a machine with few cores; unknown core count counts as strong. */
export function introBudget(cores: number | undefined, width: number): IntroBudget {
  const isLight = (cores !== undefined && cores <= FEW_CORES) || width < SMALL_VIEW_PX;
  return isLight ? LIGHT_BUDGET : FULL_BUDGET;
}
