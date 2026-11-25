/**
 * The `data-fx` attribute of the statements while an answer is revealed: "wait" (opacity 0, keeps its place),
 * "in" (fades in), none (shown for good). Once a statement has been released nothing may put it back to "wait" or
 * "in": the safety net only shows statements that are still waiting, and releasing cancels it.
 */
export interface Attributed {
  getAttribute(name: string): string | null;
  setAttribute(name: string, value: string): void;
  removeAttribute(name: string): void;
}

export const FX_ATTRIBUTE = "data-fx";

export function createStatementGate(items: readonly Attributed[], safetyMs: number) {
  const show = (el: Attributed) => {
    if (el.getAttribute(FX_ATTRIBUTE) === "wait") el.setAttribute(FX_ATTRIBUTE, "in");
  };
  let safety: ReturnType<typeof setTimeout> | undefined = setTimeout(() => items.forEach(show), safetyMs);
  items.forEach((el) => el.setAttribute(FX_ATTRIBUTE, "wait"));

  return {
    /** Starts the fade of a statement that is waiting; a released one stays as it is. */
    show,
    /** Every statement is plain again, and the safety net is gone. */
    releaseAll(): void {
      clearTimeout(safety);
      safety = undefined;
      items.forEach((el) => el.removeAttribute(FX_ATTRIBUTE));
    },
  };
}
