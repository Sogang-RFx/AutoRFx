/** Presentation mode in sessionStorage: the only thing the app keeps in browser storage. */
const KEY = "autorfx.session";

type Store = Pick<Storage, "getItem" | "setItem">;

export function writePresent(storage: Store, on: boolean): void {
  try {
    storage.setItem(KEY, JSON.stringify({ present: on }));
  } catch {
    /* storage unavailable: the mode stays in memory only */
  }
}

/** `?present=1` turns it on. A team that an older version of the app stored is removed here. */
export function readPresent(storage: Store, search: string): boolean {
  let stored = false;
  try {
    const raw = storage.getItem(KEY);
    const parsed = raw ? (JSON.parse(raw) as { present?: unknown } | null) : null;
    stored = parsed?.present === true;
    if (raw !== null) writePresent(storage, stored);
  } catch {
    /* unreadable: off, and overwritten by the next change */
  }
  return stored || new URLSearchParams(search).get("present") === "1";
}
