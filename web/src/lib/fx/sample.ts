/**
 * Text, elements and shapes to points. A point list is a Float32Array of x, y pairs in the units of the layer's
 * canvas (viewport px for the full-screen layer). The pure helpers (`thin`, `capTotal`, lines, circles, box edges)
 * run in node; the rest needs a browser and is only called from one.
 */

export type Points = Float32Array;
export type Random = () => number;

/** Text is rasterised at this scale so thin strokes survive the alpha threshold. */
const RASTER_SCALE = 2;
const INK_ALPHA = 120;
const BIG_TEXT_PX = 24;
const STEP_BIG = 2;
const STEP_SMALL = 1.5;
const BOUNDS_PAD = 4;
const LINE_STEP = 3;
const BOX_STEP = 5;
const BOX_INNER_JITTER = 4;
const MIN_BOX_PX = 4;

export type TargetKind = "text" | "line" | "box";

export const pointCount = (points: Points): number => points.length / 2;

/**
 * Keeps exactly `max` points (all of them when there are fewer), in their original order. Selection sampling:
 * the same `random` sequence always gives the same result.
 */
export function thin(points: Points, max: number, random: Random = Math.random): Points {
  const count = pointCount(points);
  const keep = Math.max(0, Math.floor(max));
  if (count <= keep) return points;
  const out = new Float32Array(keep * 2);
  let needed = keep;
  let written = 0;
  for (let i = 0; i < count && needed > 0; i += 1) {
    if (random() * (count - i) < needed) {
      out[written * 2] = points[i * 2];
      out[written * 2 + 1] = points[i * 2 + 1];
      written += 1;
      needed -= 1;
    }
  }
  return out;
}

/** Thins several lists so that together they hold at most `max` points, each keeping its share. */
export function capTotal(lists: readonly Points[], max: number, random: Random = Math.random): Points[] {
  const total = lists.reduce((sum, list) => sum + pointCount(list), 0);
  if (total <= max) return [...lists];
  const share = max / total;
  return lists.map((list) => thin(list, Math.floor(pointCount(list) * share), random));
}

export function concat(lists: readonly Points[]): Points {
  const out = new Float32Array(lists.reduce((sum, list) => sum + list.length, 0));
  let offset = 0;
  for (const list of lists) {
    out.set(list, offset);
    offset += list.length;
  }
  return out;
}

export function sampleLine(x1: number, y1: number, x2: number, y2: number, step: number = LINE_STEP): Points {
  const count = Math.max(1, Math.round(Math.hypot(x2 - x1, y2 - y1) / step));
  const out = new Float32Array((count + 1) * 2);
  for (let k = 0; k <= count; k += 1) {
    out[k * 2] = x1 + ((x2 - x1) * k) / count;
    out[k * 2 + 1] = y1 + ((y2 - y1) * k) / count;
  }
  return out;
}

export function sampleCircle(cx: number, cy: number, radius: number, step: number = LINE_STEP): Points {
  const count = Math.max(6, Math.round((2 * Math.PI * radius) / step));
  const out = new Float32Array(count * 2);
  for (let k = 0; k < count; k += 1) {
    const angle = (k / count) * 2 * Math.PI;
    out[k * 2] = cx + Math.cos(angle) * radius;
    out[k * 2 + 1] = cy + Math.sin(angle) * radius;
  }
  return out;
}

export interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** The four edges of a box, and optionally sparse dots inside it. */
export function sampleBox(rect: Rect, random: Random = Math.random, inner: number = 0): Points {
  const { left, top, right, bottom } = rect;
  if (right - left < MIN_BOX_PX || bottom - top < MIN_BOX_PX) return new Float32Array(0);
  const parts = [
    sampleLine(left, top, right, top, BOX_STEP),
    sampleLine(left, bottom, right, bottom, BOX_STEP),
    sampleLine(left, top, left, bottom, BOX_STEP),
    sampleLine(right, top, right, bottom, BOX_STEP),
  ];
  if (inner > 0) {
    const dots: number[] = [];
    for (let y = top + inner; y < bottom; y += inner) {
      for (let x = left + inner; x < right; x += inner) {
        dots.push(x + (random() - 0.5) * BOX_INNER_JITTER, y + (random() - 0.5) * BOX_INNER_JITTER);
      }
    }
    parts.push(Float32Array.from(dots));
  }
  return concat(parts);
}

/** A rule or divider: dots along its long axis through the middle. */
export function sampleRule(rect: Rect): Points {
  const horizontal = rect.right - rect.left >= rect.bottom - rect.top;
  if (horizontal) {
    const y = (rect.top + rect.bottom) / 2;
    return sampleLine(rect.left, y, rect.right, y);
  }
  const x = (rect.left + rect.right) / 2;
  return sampleLine(x, rect.top, x, rect.bottom);
}

// ── Browser only ─────────────────────────────────────────────────────────────────────────────────────────────────

interface Bounds {
  x: number;
  y: number;
  w: number;
  h: number;
}

type TextContext = CanvasRenderingContext2D & { letterSpacing?: string };

function setSpacing(ctx: TextContext, value: string): void {
  if ("letterSpacing" in ctx) ctx.letterSpacing = value === "normal" ? "0px" : value;
}

/** Draws with `draw` on an offscreen canvas covering `bounds` and returns the inked grid points. */
function inkedPoints(bounds: Bounds, step: number, draw: (ctx: TextContext) => void): Points {
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(bounds.w * RASTER_SCALE);
  canvas.height = Math.ceil(bounds.h * RASTER_SCALE);
  if (!canvas.width || !canvas.height) return new Float32Array(0);
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return new Float32Array(0);
  ctx.scale(RASTER_SCALE, RASTER_SCALE);
  ctx.translate(-bounds.x, -bounds.y);
  ctx.fillStyle = "#000";
  draw(ctx);
  const { data, width, height } = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const jump = Math.max(1, Math.round(step * RASTER_SCALE));
  const found: number[] = [];
  for (let py = 0; py < height; py += jump) {
    for (let px = 0; px < width; px += jump) {
      if (data[(py * width + px) * 4 + 3] > INK_ALPHA) found.push(bounds.x + px / RASTER_SCALE, bounds.y + py / RASTER_SCALE);
    }
  }
  return Float32Array.from(found);
}

export interface TextOptions {
  font: string;
  size: number;
  /** Centre of the text. */
  cx: number;
  cy: number;
  letterSpacing?: string;
  step?: number;
  max?: number;
  random?: Random;
}

/** Dots where a string would be drawn in `font`, centred on (cx, cy). */
export function sampleText(text: string, opts: TextOptions): Points {
  const probe = document.createElement("canvas").getContext("2d") as TextContext | null;
  if (!probe) return new Float32Array(0);
  probe.font = opts.font;
  setSpacing(probe, opts.letterSpacing ?? "normal");
  const w = probe.measureText(text).width + 12;
  const h = opts.size * 1.5;
  const step = opts.step ?? (opts.size >= BIG_TEXT_PX ? STEP_BIG : STEP_SMALL);
  const points = inkedPoints({ x: opts.cx - w / 2, y: opts.cy - h / 2, w, h }, step, (ctx) => {
    ctx.font = opts.font;
    setSpacing(ctx, opts.letterSpacing ?? "normal");
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, opts.cx, opts.cy);
  });
  return opts.max === undefined ? points : thin(points, opts.max, opts.random);
}

function drawWords(ctx: TextContext, root: Element): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const parent = node.parentElement;
    if (!parent || !node.nodeValue) continue;
    const style = getComputedStyle(parent);
    ctx.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    setSpacing(ctx, style.letterSpacing);
    const words = /\S+/g;
    for (let match = words.exec(node.nodeValue); match; match = words.exec(node.nodeValue)) {
      range.setStart(node, match.index);
      range.setEnd(node, match.index + match[0].length);
      const rect = range.getBoundingClientRect();
      if (!rect.width) continue;
      const word = applyTransform(match[0], style.textTransform);
      const ascent = ctx.measureText(word).fontBoundingBoxAscent;
      ctx.textBaseline = ascent ? "alphabetic" : "middle";
      ctx.fillText(word, rect.left, ascent ? rect.top + ascent : rect.top + rect.height / 2);
    }
  }
}

function applyTransform(word: string, transform: string): string {
  if (transform === "uppercase") return word.toUpperCase();
  if (transform === "lowercase") return word.toLowerCase();
  if (transform === "capitalize") return word.charAt(0).toUpperCase() + word.slice(1);
  return word;
}

/**
 * Dots on the real glyphs of `el`: each word is drawn where the browser put it, with the computed font, letter
 * spacing and text transform, so the dots land on the text exactly.
 */
export function sampleElementText(el: Element, max?: number, random?: Random): Points {
  const box = el.getBoundingClientRect();
  if (!box.width || !box.height) return new Float32Array(0);
  const fontPx = parseFloat(getComputedStyle(el).fontSize) || 16;
  const bounds = { x: box.left - BOUNDS_PAD, y: box.top - BOUNDS_PAD, w: box.width + BOUNDS_PAD * 2, h: box.height + BOUNDS_PAD * 2 };
  const points = inkedPoints(bounds, fontPx >= BIG_TEXT_PX ? STEP_BIG : STEP_SMALL, (ctx) => drawWords(ctx, el));
  return max === undefined ? points : thin(points, max, random);
}

/** Points for an element by its `data-particle-target` kind: glyphs, a rule, or the edges of its box. */
export function sampleTarget(el: Element, kind: TargetKind, max: number, random: Random = Math.random): Points {
  if (kind === "text") return sampleElementText(el, max, random);
  const rect = el.getBoundingClientRect();
  const points = kind === "line" ? sampleRule(rect) : sampleBox(rect, random);
  return thin(points, max, random);
}

export function targetKind(el: Element): TargetKind {
  const value = el.getAttribute("data-particle-target");
  return value === "line" || value === "box" ? value : "text";
}

