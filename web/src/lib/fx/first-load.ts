/**
 * Hard load versus client navigation (docs/design/immersive-ui.md §3.1).
 *
 * After a full page load the server HTML is already painted when hydration runs. Hiding a heading then to fly dots
 * at it would show the text, blank it and redraw it: a flash. So `useReveal` skips elements that come from the
 * first commit of the document and only reveals what mounts later (client-side navigation, or content that was not
 * in the server HTML). The controller itself never skips: callers that use it directly (the intro) always run.
 *
 * The first commit is the one in which the provider's layout effect runs; its children's layout effects run just
 * before it. The window closes on the next timer tick, which is after React's Strict Mode re-run of the same commit.
 */

export interface LoadPhase {
  /** True until the first commit of the document has been painted from server HTML. */
  isFirstCommit: boolean;
}

/** The decision: reveal an element that has just mounted, or leave the server-rendered text alone. */
export function shouldRevealOnMount(phase: LoadPhase): boolean {
  return !phase.isFirstCommit;
}

export interface LoadGate {
  /** The provider calls this in its layout effect: the first commit ends on the next tick. Safe to repeat. */
  endFirstCommit(): void;
  shouldReveal(): boolean;
}

export function createLoadGate(schedule: (fn: () => void) => void = (fn) => void setTimeout(fn, 0)): LoadGate {
  const phase: LoadPhase = { isFirstCommit: true };
  let isScheduled = false;
  return {
    endFirstCommit() {
      if (isScheduled) return;
      isScheduled = true;
      schedule(() => {
        phase.isFirstCommit = false;
      });
    },
    shouldReveal: () => shouldRevealOnMount(phase),
  };
}

/** The one gate of this document. */
export const loadGate: LoadGate = createLoadGate();
