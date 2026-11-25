/**
 * Where everything sits, as numbers. Pure and deterministic: the same model always gives the same
 * positions. Units are grid cells; one clause box is `BOX` wide inside a cell of 1.
 *
 * Files are thin sheets on the left, each file's clauses a compact block of boxes in the middle,
 * departments on the right. Inside a block the clauses the account may see come first, in the order
 * of the response, and the ones it may not see follow on rows of their own. So a hidden clause's
 * place is a function of the counts only, never of what it says (INV-8).
 */
import type { TopoModel } from "./model";

export const BOX = 0.72;
/** Blocks are wider than tall, so a thousand clauses in one file stay readable. */
const BLOCK_ASPECT = 1.6;
const FILE_GAP = 2.5;
const SHEET_GAP = 2;
const SHEET_THICKNESS = 0.3;
const DEPT_GAP = 1.2;
const DEPT_MIN_HEIGHT = 1.6;
const DEPT_SHEET_THICKNESS = 0.3;
const DEPT_OFFSET_MIN = 6;
const DEPT_OFFSET_SHARE = 0.4;

/** Center and size, in cells. */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Bounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

export interface Layout {
  /** x, y of each clause box center, two numbers per clause in model order. */
  clauseXY: Float32Array;
  /** Thin sheets, one per file / department, in model order. */
  files: Rect[];
  departments: Rect[];
  /** x1, y1, x2, y2 per edge in model order: from the right face of the box to the department sheet. */
  edges: Float32Array;
  bounds: Bounds;
}

interface Block {
  cols: number;
  rows: number;
  /** Clause indices in drawing order: the visible ones, then the hidden ones. */
  order: number[];
  visibleCount: number;
}

function blockOf(model: TopoModel, file: number): Block {
  const mine = model.clauses.map((c, i) => ({ c, i })).filter((e) => e.c.file === file);
  const visible = mine.filter((e) => e.c.isVisible).map((e) => e.i);
  const hidden = mine.filter((e) => !e.c.isVisible).map((e) => e.i);
  const n = Math.max(1, mine.length);
  const cols = Math.max(1, Math.min(n, Math.ceil(Math.sqrt(n * BLOCK_ASPECT))));
  const rows = Math.ceil(visible.length / cols) + Math.ceil(hidden.length / cols);
  return { cols, rows: Math.max(1, rows), order: [...visible, ...hidden], visibleCount: visible.length };
}

/** Row-major cell of the k-th clause; the hidden ones start on a fresh row. */
function cellOf(block: Block, k: number): { col: number; row: number } {
  const { visibleCount } = block;
  if (k < visibleCount) return { col: k % block.cols, row: Math.floor(k / block.cols) };
  const h = k - visibleCount;
  return { col: h % block.cols, row: Math.ceil(visibleCount / block.cols) + Math.floor(h / block.cols) };
}

function placeBlocks(model: TopoModel, blocks: Block[], width: number, height: number) {
  const clauseXY = new Float32Array(model.clauses.length * 2);
  const files: Rect[] = [];
  let top = height / 2;
  blocks.forEach((block) => {
    const left = -width / 2;
    block.order.forEach((clauseIndex, k) => {
      const { col, row } = cellOf(block, k);
      clauseXY[clauseIndex * 2] = left + col + 0.5;
      clauseXY[clauseIndex * 2 + 1] = top - row - 0.5;
    });
    files.push({ x: left - SHEET_GAP, y: top - block.rows / 2, w: SHEET_THICKNESS, h: block.rows });
    top -= block.rows + FILE_GAP;
  });
  return { clauseXY, files };
}

function placeDepartments(model: TopoModel, height: number, x: number): Rect[] {
  const n = model.departments.length;
  if (n === 0) return [];
  const totalLead = model.edges.length;
  const free = Math.max(0, height - DEPT_GAP * (n - 1));
  const heights = model.departments.map((d) =>
    Math.max(DEPT_MIN_HEIGHT, totalLead === 0 ? 0 : (free * d.lead) / totalLead),
  );
  const column = heights.reduce((a, b) => a + b, 0) + DEPT_GAP * (n - 1);
  let top = column / 2;
  return heights.map((h) => {
    const rect = { x, y: top - h / 2, w: DEPT_SHEET_THICKNESS, h };
    top -= h + DEPT_GAP;
    return rect;
  });
}

function placeEdges(model: TopoModel, clauseXY: Float32Array, depts: Rect[]): Float32Array {
  const seen = new Array<number>(depts.length).fill(0);
  const out = new Float32Array(model.edges.length * 4);
  model.edges.forEach((e, i) => {
    const d = depts[e.department];
    const slots = model.departments[e.department].lead;
    const slot = seen[e.department]++;
    out[i * 4] = clauseXY[e.clause * 2] + BOX / 2;
    out[i * 4 + 1] = clauseXY[e.clause * 2 + 1];
    out[i * 4 + 2] = d.x - d.w / 2;
    out[i * 4 + 3] = d.y + d.h / 2 - ((slot + 0.5) / slots) * d.h;
  });
  return out;
}

function boundsOf(rects: Rect[], clauseXY: Float32Array): Bounds {
  const b = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
  for (const r of rects) {
    b.minX = Math.min(b.minX, r.x - r.w / 2);
    b.maxX = Math.max(b.maxX, r.x + r.w / 2);
    b.minY = Math.min(b.minY, r.y - r.h / 2);
    b.maxY = Math.max(b.maxY, r.y + r.h / 2);
  }
  for (let i = 0; i < clauseXY.length; i += 2) {
    b.minX = Math.min(b.minX, clauseXY[i] - BOX / 2);
    b.maxX = Math.max(b.maxX, clauseXY[i] + BOX / 2);
  }
  return Number.isFinite(b.minX) ? b : { minX: -1, maxX: 1, minY: -1, maxY: 1 };
}

export function computeLayout(model: TopoModel): Layout {
  const blocks = model.files.map((_, f) => blockOf(model, f));
  const width = Math.max(1, ...blocks.map((b) => b.cols));
  const height = blocks.reduce((sum, b) => sum + b.rows, 0) + FILE_GAP * Math.max(0, blocks.length - 1);
  const { clauseXY, files } = placeBlocks(model, blocks, width, height);
  const deptX = width / 2 + Math.max(DEPT_OFFSET_MIN, width * DEPT_OFFSET_SHARE);
  const departments = placeDepartments(model, height, deptX);
  const edges = placeEdges(model, clauseXY, departments);
  return { clauseXY, files, departments, edges, bounds: boundsOf([...files, ...departments], clauseXY) };
}
