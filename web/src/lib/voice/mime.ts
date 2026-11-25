/** The recording formats, best first. All of them are on the list the server accepts (docs/design/immersive-ui.md §6.1). */
export const RECORDING_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
  "audio/ogg",
] as const;

/** The first format this browser can record, chosen at run time; null when it can record none. */
export function pickMimeType(isSupported: ((type: string) => boolean) | null): string | null {
  if (!isSupported) return null;
  return RECORDING_TYPES.find((type) => isSupported(type)) ?? null;
}
