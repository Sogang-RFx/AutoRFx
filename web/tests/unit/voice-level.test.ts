/** The input level and the choice of recording format: pure parts of the recorder and the player. */
import { describe, expect, it } from "vitest";
import { ATTACK_RATE, RELEASE_RATE, inputLevel, smoothInputLevel } from "@/lib/voice/level";
import { pickMimeType } from "@/lib/voice/mime";
import { READ_ALOUD_KEY, readReadAloud, writeReadAloud } from "@/lib/voice/prefs";

const bytes = (...v: number[]) => Uint8Array.from(v);

describe("inputLevel", () => {
  it("is 0 for silence and for noise under the gate", () => {
    expect(inputLevel(bytes(128, 128, 128, 128))).toBe(0);
    expect(inputLevel(bytes(129, 127, 128, 129))).toBe(0);
    expect(inputLevel(new Uint8Array(0))).toBe(0);
  });

  it("grows with the loudness and never leaves 0..1", () => {
    const soft = inputLevel(bytes(138, 118, 138, 118));
    const loud = inputLevel(bytes(188, 68, 188, 68));
    expect(soft).toBeGreaterThan(0);
    expect(loud).toBeGreaterThan(soft);
    expect(inputLevel(bytes(255, 0, 255, 0))).toBe(1);
  });
});

describe("smoothInputLevel", () => {
  it("rises faster than it falls", () => {
    const up = smoothInputLevel(0, 1, 0.05);
    const down = 1 - smoothInputLevel(1, 0, 0.05);
    expect(ATTACK_RATE).toBeGreaterThan(RELEASE_RATE);
    expect(up).toBeGreaterThan(down);
  });

  it("stays between the two values and settles on the target", () => {
    expect(smoothInputLevel(0.2, 0.8, 0.016)).toBeGreaterThan(0.2);
    expect(smoothInputLevel(0.2, 0.8, 0.016)).toBeLessThan(0.8);
    expect(smoothInputLevel(0.2, 0.8, 10)).toBeCloseTo(0.8, 5);
    expect(smoothInputLevel(0.3, 0.3, 0.1)).toBe(0.3);
  });

  it("does not move with no time, and ignores a negative or missing step", () => {
    expect(smoothInputLevel(0.4, 1, 0)).toBe(0.4);
    expect(smoothInputLevel(0.4, 1, -1)).toBe(0.4);
    expect(smoothInputLevel(0.4, 1, Number.NaN)).toBe(0.4);
  });
});

describe("pickMimeType", () => {
  const supports = (...ok: string[]) => (type: string) => ok.includes(type);

  it("takes the first format the browser can record, in the order of preference", () => {
    expect(pickMimeType(supports("audio/webm;codecs=opus", "audio/webm", "audio/mp4"))).toBe("audio/webm;codecs=opus");
    expect(pickMimeType(supports("audio/mp4"))).toBe("audio/mp4");
    expect(pickMimeType(supports("audio/ogg;codecs=opus"))).toBe("audio/ogg;codecs=opus");
  });

  it("only offers formats the server accepts", () => {
    expect(pickMimeType(supports("video/webm", "audio/aac"))).toBeNull();
  });

  it("returns null when the browser cannot say or has none", () => {
    expect(pickMimeType(() => false)).toBeNull();
    expect(pickMimeType(null)).toBeNull();
  });
});

describe("the read-aloud switch", () => {
  const memory = (initial: Record<string, string> = {}) => {
    const data = { ...initial };
    return {
      data,
      getItem: (k: string) => data[k] ?? null,
      setItem: (k: string, v: string) => void (data[k] = v),
    };
  };

  it("is off until it is turned on, and remembers the choice", () => {
    const store = memory();
    expect(readReadAloud(store)).toBe(false);
    writeReadAloud(true, store);
    expect(store.data[READ_ALOUD_KEY]).toBe("1");
    expect(readReadAloud(store)).toBe(true);
    writeReadAloud(false, store);
    expect(readReadAloud(store)).toBe(false);
  });

  it("works when the storage is missing or throws", () => {
    const blocked = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(readReadAloud(blocked)).toBe(false);
    expect(() => writeReadAloud(true, blocked)).not.toThrow();
    expect(readReadAloud(null)).toBe(false);
    expect(() => writeReadAloud(true, null)).not.toThrow();
  });
});
