/** Where the camera starts: far enough that the whole map fits, with room in the margins for the DOM labels. */
import type { Bounds } from "./layout";

/** Width reserved on each side for the file and department names, at most. */
export const GUTTER_PX = 150;
/** Share of the canvas width the gutter may take on a narrow screen. */
const GUTTER_MAX_SHARE = 0.26;
const PAD_Y_PX = 28;
/** Keeps the nearest boxes off the clipping plane when the camera is turned. */
const DEPTH_ALLOWANCE = 1.15;

export interface FitInput {
  bounds: Bounds;
  widthPx: number;
  heightPx: number;
  fovDeg: number;
}

export interface CameraFit {
  /** Look-at point (x, y) in cells. */
  target: [number, number];
  distance: number;
  gutterPx: number;
}

export const gutterFor = (widthPx: number): number => Math.min(GUTTER_PX, Math.round(widthPx * GUTTER_MAX_SHARE));

/** Straight-on view: the smallest distance at which both the width and the height of the map fit. */
export function fitCamera({ bounds, widthPx, heightPx, fovDeg }: FitInput): CameraFit {
  const gutterPx = gutterFor(widthPx);
  const worldW = Math.max(1e-6, bounds.maxX - bounds.minX);
  const worldH = Math.max(1e-6, bounds.maxY - bounds.minY);
  const availW = Math.max(1, widthPx - 2 * gutterPx);
  const availH = Math.max(1, heightPx - 2 * PAD_Y_PX);
  const tan = Math.tan((fovDeg * Math.PI) / 360);
  // On screen, the visible height at distance d is 2 d tan, spread over heightPx pixels.
  const forWidth = (worldW * heightPx) / (2 * tan * availW);
  const forHeight = (worldH * heightPx) / (2 * tan * availH);
  return {
    target: [(bounds.minX + bounds.maxX) / 2, (bounds.minY + bounds.maxY) / 2],
    distance: Math.max(forWidth, forHeight) * DEPTH_ALLOWANCE,
    gutterPx,
  };
}

/** World point to canvas pixels for a straight-on camera at the fitted distance (used by the tests to check what fits). */
export function worldToPixel(
  point: [number, number],
  fit: CameraFit,
  size: { widthPx: number; heightPx: number },
  fovDeg: number,
): [number, number] {
  const tan = Math.tan((fovDeg * Math.PI) / 360);
  const perUnit = size.heightPx / (2 * fit.distance * tan);
  return [size.widthPx / 2 + (point[0] - fit.target[0]) * perUnit, size.heightPx / 2 - (point[1] - fit.target[1]) * perUnit];
}
