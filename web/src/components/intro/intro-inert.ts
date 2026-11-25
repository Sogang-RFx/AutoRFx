/**
 * Keeps the application underneath the intro out of reach of the keyboard and of assistive technology while the
 * cover is up. `inert` is a plain attribute, so releasing it is idempotent and safe to call from every ending path.
 */
export const APP_ROOT_ID = "app-root";

export interface InertTarget {
  setAttribute(name: string, value: string): void;
  removeAttribute(name: string): void;
}

/** Makes `target` inert and returns the function that undoes it (a missing target is a no-op). */
export function holdInert(target: InertTarget | null): () => void {
  if (!target) return () => {};
  target.setAttribute("inert", "");
  return () => target.removeAttribute("inert");
}
