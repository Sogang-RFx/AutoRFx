/** Intro decisions and timeline: whether to play, the phase times, the name layout and the guards. */
import { describe, expect, it } from "vitest";
import {
  INTRO_SEEN_KEY,
  INTRO_TIMING,
  introBudget,
  introTimeline,
  markIntroSeen,
  nameLayout,
  readIntroSeen,
  resizeEndsIntro,
  shouldPlayIntro,
  taglineLines,
  TAGLINE,
} from "@/components/intro/intro-plan";
import { COVER_FADE_MS, HANDOFF_AT, REVEAL_FADE_MS } from "@/lib/fx/motion";
import type { StorageLike } from "@/lib/fx/prefs";

const base = { search: "", seen: false, moves: true };

describe("shouldPlayIntro", () => {
  it("plays on the first visit of a session and not again once seen", () => {
    expect(shouldPlayIntro(base)).toBe(true);
    expect(shouldPlayIntro({ ...base, seen: true })).toBe(false);
  });

  it("never plays when nothing may move, whatever else is asked", () => {
    expect(shouldPlayIntro({ ...base, moves: false })).toBe(false);
    expect(shouldPlayIntro({ ...base, moves: false, search: "?intro=1" })).toBe(false);
    expect(shouldPlayIntro({ ...base, moves: false, replay: true })).toBe(false);
  });

  it("?intro=0 turns it off and ?intro=1 plays it even when it was seen", () => {
    expect(shouldPlayIntro({ ...base, search: "?intro=0" })).toBe(false);
    expect(shouldPlayIntro({ ...base, seen: true, search: "?a=b&intro=1" })).toBe(true);
    expect(shouldPlayIntro({ ...base, seen: true, search: "?intro=yes" })).toBe(false);
  });

  it("a replay request always plays, also after ?intro=0", () => {
    expect(shouldPlayIntro({ ...base, seen: true, search: "?intro=0", replay: true })).toBe(true);
  });
});

describe("seen flag", () => {
  const memory = (): StorageLike & { data: Map<string, string> } => {
    const data = new Map<string, string>();
    return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
  };

  it("is written once the intro has ended and read back", () => {
    const store = memory();
    expect(readIntroSeen(() => store)).toBe(false);
    markIntroSeen(() => store);
    expect(store.data.get(INTRO_SEEN_KEY)).toBe("1");
    expect(readIntroSeen(() => store)).toBe(true);
  });

  it("treats blocked or throwing storage as not seen and does not throw", () => {
    const blocked = () => {
      throw new Error("denied");
    };
    expect(readIntroSeen(blocked)).toBe(false);
    expect(readIntroSeen(() => null)).toBe(false);
    expect(() => markIntroSeen(blocked)).not.toThrow();
    expect(() => markIntroSeen(() => null)).not.toThrow();
  });
});

describe("introTimeline", () => {
  it("runs sphere, name, hold, hand-off in order", () => {
    const t = introTimeline(4);
    expect(t.nameAt).toBe(INTRO_TIMING.sphereMs);
    expect(t.nameLandedAt).toBeGreaterThan(t.nameAt);
    expect(t.handoffAt).toBe(t.nameLandedAt + INTRO_TIMING.holdMs);
    expect(t.showAt).toBeGreaterThan(t.handoffAt);
    expect(t.endAt).toBeGreaterThan(t.showAt);
  });

  it("shows the real text at the hand-off share of the flight", () => {
    const t = introTimeline(1);
    const reach = INTRO_TIMING.handoffMs + INTRO_TIMING.handoffJitterMs;
    expect(t.showAt - t.handoffAt).toBeCloseTo(reach * HANDOFF_AT, 5);
    expect(t.endAt - t.handoffAt).toBeCloseTo(reach + REVEAL_FADE_MS, 5);
  });

  it("longer for more elements, and the cover is always gone before the end", () => {
    expect(introTimeline(6).endAt).toBeGreaterThan(introTimeline(2).endAt);
    expect(introTimeline(0).endAt).toBe(introTimeline(1).endAt);
    for (const n of [0, 1, 5, 12]) {
      const t = introTimeline(n);
      expect(t.handoffAt + COVER_FADE_MS).toBeLessThanOrEqual(t.endAt);
    }
  });

  it("waiting for the screen pushes the hand-off and nothing before it", () => {
    const early = introTimeline(3);
    const late = introTimeline(3, 800);
    expect(late.nameLandedAt).toBe(early.nameLandedAt);
    expect(late.handoffAt).toBe(early.handoffAt + 800);
  });
});

describe("name layout", () => {
  it("keeps the word and the tagline inside the viewport at every width", () => {
    for (const [w, h] of [
      [320, 568],
      [768, 1024],
      [1440, 900],
      [1920, 1080],
    ]) {
      const l = nameLayout(w, h);
      expect(l.cx).toBe(w / 2);
      expect(l.wordSize).toBeGreaterThanOrEqual(44);
      expect(l.wordY).toBeGreaterThan(0);
      const last = l.tagLines[l.tagLines.length - 1];
      expect(last.y).toBeLessThan(h);
      expect(l.tagSize).toBeGreaterThanOrEqual(12);
      expect(l.tagSize).toBeLessThanOrEqual(22);
    }
  });

  it("puts the tagline below the word, line after line", () => {
    const l = nameLayout(375, 700);
    expect(l.tagLines[0].y).toBeGreaterThan(l.wordY);
    for (let i = 1; i < l.tagLines.length; i += 1) expect(l.tagLines[i].y).toBeGreaterThan(l.tagLines[i - 1].y);
  });

  it("splits the tagline at the comma on a narrow screen and keeps one line on a wide one", () => {
    expect(taglineLines(TAGLINE, 22, 1200)).toEqual([TAGLINE]);
    const narrow = taglineLines(TAGLINE, 16, 260);
    expect(narrow).toHaveLength(2);
    expect(narrow[0].endsWith(",")).toBe(true);
    expect(narrow.join(" ")).toBe(TAGLINE);
  });

  it("splits at a space when there is no comma", () => {
    expect(taglineLines("alpha beta gamma delta", 20, 150)).toEqual(["alpha beta", "gamma delta"]);
  });
});

describe("guards", () => {
  it("a resize that changes the width or a lot of height ends the intro; a bar sliding does not", () => {
    expect(resizeEndsIntro({ w: 1000, h: 700 }, { w: 1000, h: 700 })).toBe(false);
    expect(resizeEndsIntro({ w: 1000, h: 700 }, { w: 1000, h: 660 })).toBe(false);
    expect(resizeEndsIntro({ w: 1000, h: 700 }, { w: 1000, h: 500 })).toBe(true);
    expect(resizeEndsIntro({ w: 1000, h: 700 }, { w: 900, h: 700 })).toBe(true);
  });

  it("gives smaller phones and weaker machines fewer dots, never more than the layer holds", () => {
    const strong = introBudget(10, 1440);
    expect(introBudget(2, 1440).name).toBeLessThan(strong.name);
    expect(introBudget(10, 375).handoff).toBeLessThan(strong.handoff);
    expect(introBudget(undefined, 1440)).toEqual(strong);
    expect(strong.sphere).toBe(1650);
    expect(strong.name).toBeLessThanOrEqual(9000);
  });
});
