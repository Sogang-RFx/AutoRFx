/** Colours for the scene, read from the CSS tokens when the scene starts, so the map never has colours of its own. */
export interface Palette {
  ink: string;
  muted: string;
  accent: string;
  danger: string;
  line: string;
  lineStrong: string;
  canvas: string;
}

const TOKENS: Record<keyof Palette, string> = {
  ink: "--color-ink",
  muted: "--color-ink-muted",
  accent: "--color-accent",
  danger: "--color-danger",
  line: "--color-line",
  lineStrong: "--color-line-control",
  canvas: "--color-canvas",
};

export function readPalette(root: Element = document.documentElement): Palette {
  const style = getComputedStyle(root);
  const read = (token: string): string => style.getPropertyValue(token).trim();
  const entries = (Object.keys(TOKENS) as (keyof Palette)[]).map((k) => [k, read(TOKENS[k])] as const);
  const missing = entries.filter(([, v]) => v === "").map(([k]) => k);
  if (missing.length > 0) throw new Error(`Missing colour tokens: ${missing.join(", ")}`);
  return Object.fromEntries(entries) as unknown as Palette;
}
