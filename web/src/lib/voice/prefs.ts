/** The "답변 읽어 주기" switch, remembered in `localStorage`. Off by default; a blocked store only means it is not remembered. */
export const READ_ALOUD_KEY = "autorfx.voice.readAloud";

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** The browser's storage, or null where it is missing or blocked (accessing it may throw). */
export function browserStorage(): StorageLike | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function readReadAloud(storage: StorageLike | null = browserStorage()): boolean {
  try {
    return storage?.getItem(READ_ALOUD_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeReadAloud(on: boolean, storage: StorageLike | null = browserStorage()): void {
  try {
    storage?.setItem(READ_ALOUD_KEY, on ? "1" : "0");
  } catch {
    // Not remembered; the switch still works on this page.
  }
}
