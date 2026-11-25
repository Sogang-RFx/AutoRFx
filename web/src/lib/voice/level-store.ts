/**
 * The voice level as a tiny external store. It is read by the sphere alone (`useSyncExternalStore`), so a level
 * that changes up to 20 times a second never re-renders the chat page and the source viewer in it.
 */
export interface LevelSource {
  get(): number;
  subscribe(listener: () => void): () => void;
}

export interface LevelStore extends LevelSource {
  set(level: number): void;
}

export function createLevelStore(): LevelStore {
  let level = 0;
  const listeners = new Set<() => void>();
  return {
    get: () => level,
    subscribe(listener) {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
    set(next) {
      const clamped = Number.isFinite(next) ? Math.min(1, Math.max(0, next)) : 0;
      if (clamped === level) return;
      level = clamped;
      listeners.forEach((l) => l());
    },
  };
}
