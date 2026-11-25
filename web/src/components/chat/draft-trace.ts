/**
 * Where the rows of dots of the waiting state were, so the dots of the answer that replaces them start there. The
 * waiting state is gone from the page when the answer arrives; it saves the rows on every render, in the log's own
 * coordinates (they stay right when the log scrolls to the new answer). One store per conversation; a trace is used
 * only by the answer of the turn it was saved for.
 */
import { boxOf, fromContent, toContent, type Box, type Scroller } from "./dot-origin";

export const PANEL_ATTRIBUTE = "data-fx-panel";
export const PANEL_SELECTOR = `[${PANEL_ATTRIBUTE}]`;

export interface DraftTrace {
  save(turnId: number, rows: readonly Box[]): void;
  /** The saved rows of this turn, once; an empty list when there are none or they belong to another turn. */
  take(turnId: number): Box[];
}

export function createDraftTrace(): DraftTrace {
  let saved: { turnId: number; rows: Box[] } | null = null;
  return {
    save: (turnId, rows) => {
      saved = { turnId, rows: [...rows] };
    },
    take: (turnId) => {
      const found = saved?.turnId === turnId ? saved.rows : [];
      saved = null;
      return found;
    },
  };
}

export function scrollerOf(el: Element): Scroller {
  const r = el.getBoundingClientRect();
  return { left: r.left, top: r.top, scrollLeft: el.scrollLeft, scrollTop: el.scrollTop };
}

/** The rows (children of `container`) as positions in the content of `panel`. */
export function captureRows(container: Element, panel: Element): Box[] {
  const scroller = scrollerOf(panel);
  return [...container.children].map((row) => toContent(boxOf(row.getBoundingClientRect()), scroller));
}

/** Saved rows as viewport boxes, where they are now. */
export function restoreRows(rows: readonly Box[], panel: Element): Box[] {
  const scroller = scrollerOf(panel);
  return rows.map((row) => fromContent(row, scroller));
}
