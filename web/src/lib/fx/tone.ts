/** Dot tones. Colours are never stored here: they are read from the CSS tokens when a layer starts. */

export const TONE = { ink: 0, muted: 1, accent: 2, danger: 3 } as const;
export type Tone = (typeof TONE)[keyof typeof TONE];

/** Same order as `TONE`. */
export const TONE_TOKENS = ["--color-ink", "--color-ink-muted", "--color-accent", "--color-danger"] as const;

/** Share of dots drawn in the muted tone when nothing else decides. */
export const MUTED_SHARE = 0.22;
