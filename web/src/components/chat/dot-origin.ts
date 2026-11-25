/**
 * Where the dots of an answer may be: only inside the conversation panel (docs/design/immersive-ui.md §2, rule 2:
 * no particles over reading surfaces, and the source viewer is one). Boxes are in viewport px. Pure, so the rule is
 * tested without a browser.
 */
import type { Points } from "@/lib/fx/sample";

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** A scroller's box and how far it has scrolled; rows are saved relative to its content. */
export interface Scroller {
  left: number;
  top: number;
  scrollLeft: number;
  scrollTop: number;
}

/** Fractions of 1 with no short cycle: successive k spread over a box without a random source. */
const SPREAD_X = 0.6180339887;
const SPREAD_Y = 0.7548776662;

const frac = (v: number): number => v - Math.floor(v);

export function clampPoint(x: number, y: number, box: Box): [number, number] {
  return [Math.min(box.right, Math.max(box.left, x)), Math.min(box.bottom, Math.max(box.top, y))];
}

/** The k-th start point: spread over the sources (one after the other), then pulled into the panel. */
export function startPointFor(k: number, sources: readonly Box[], panel: Box): [number, number] {
  const source = sources.length > 0 ? sources[k % sources.length] : panel;
  const round = Math.floor(k / Math.max(1, sources.length));
  const x = source.left + frac((round + 1) * SPREAD_X) * (source.right - source.left);
  const y = source.top + frac((round + 1) * SPREAD_Y) * (source.bottom - source.top);
  return clampPoint(x, y, panel);
}

/** The target points (x, y pairs) that lie inside `box`; the others are not drawn. */
export function keepInside(points: Points, box: Box): Points {
  const kept: number[] = [];
  for (let i = 0; i + 1 < points.length; i += 2) {
    const x = points[i];
    const y = points[i + 1];
    if (x >= box.left && x <= box.right && y >= box.top && y <= box.bottom) kept.push(x, y);
  }
  return Float32Array.from(kept);
}

/** CSS `clip-path` that cuts the full-screen layer down to the panel, whatever path a dot takes. */
export function clipInset(panel: Box, viewport: { width: number; height: number }): string {
  const px = (v: number) => `${Math.max(0, Math.round(v))}px`;
  const { top, right, bottom, left } = {
    top: px(panel.top),
    right: px(viewport.width - panel.right),
    bottom: px(viewport.height - panel.bottom),
    left: px(panel.left),
  };
  return `inset(${top} ${right} ${bottom} ${left})`;
}

/** A viewport box as a position in the scroller's content. */
export function toContent(box: Box, scroller: Scroller): Box {
  const dx = scroller.scrollLeft - scroller.left;
  const dy = scroller.scrollTop - scroller.top;
  return { left: box.left + dx, right: box.right + dx, top: box.top + dy, bottom: box.bottom + dy };
}

/** A position in the scroller's content as a viewport box, after the scroller has moved or scrolled. */
export function fromContent(box: Box, scroller: Scroller): Box {
  const dx = scroller.left - scroller.scrollLeft;
  const dy = scroller.top - scroller.scrollTop;
  return { left: box.left + dx, right: box.right + dx, top: box.top + dy, bottom: box.bottom + dy };
}

export const boxOf = (r: { left: number; top: number; right: number; bottom: number }): Box => ({
  left: r.left,
  top: r.top,
  right: r.right,
  bottom: r.bottom,
});

export const intersects = (a: Box, b: Box): boolean =>
  a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
