/**
 * Honest translation labels (TRANS-03): the screen and the exports name the
 * translation engine as it is, and say why a clause has no translation.
 * All values here are invented for the test.
 */
import { describe, expect, it } from "vitest";
import type { Engines } from "@/lib/rfq/contract";
import { exportNote, rtmCsv } from "@/lib/rfq/export";
import { noTranslationMessage, translationLabel } from "@/lib/rfq/translation";

const ENGINES: Engines = {
  analysis: "rule-only",
  translation: "none",
  retrieval: "bm25",
  llm_connected: false,
};

describe("translationLabel", () => {
  it("names no translation", () => {
    expect(translationLabel("none")).toBe("번역 없음");
  });
  it("strips the openai prefix", () => {
    expect(translationLabel("openai:gpt-x")).toBe("gpt-x");
  });
  it("leaves other ids unchanged", () => {
    expect(translationLabel("fixture")).toBe("fixture");
  });
});

describe("noTranslationMessage", () => {
  it("does not claim that a clause labelled Korean is Korean", () => {
    expect(noTranslationMessage("ko", "openai:gpt-x")).toBe(
      "원문이 국문이거나 번역을 제공하지 않는 조항입니다. 원문을 확인하세요.",
    );
  });
  it("says no provider is connected, whatever the language", () => {
    expect(noTranslationMessage("ko", "none")).toBe("번역 없음. 번역 제공자가 연결되지 않아 원문만 제공합니다.");
    expect(noTranslationMessage("en", "none")).toBe("번역 없음. 번역 제공자가 연결되지 않아 원문만 제공합니다.");
  });
  it("does not claim a missing provider otherwise", () => {
    const text = "이 조항은 번역을 제공하지 않습니다. 원문을 확인하세요.";
    expect(noTranslationMessage("de", "openai:gpt-x")).toBe(text);
    expect(noTranslationMessage("en", "")).toBe(text);
  });
});

describe("export note", () => {
  it("shows the translation label, not the raw id", () => {
    expect(exportNote({ ...ENGINES, translation: "openai:gpt-x" })).toContain("번역: gpt-x");
    expect(exportNote(ENGINES)).toContain("번역: 번역 없음");
  });
  it("keeps the raw none out of the CSV first line", () => {
    const first = rtmCsv([], { dept: (i) => i, file: (i) => i }, exportNote(ENGINES)).split("\n")[0];
    expect(first).not.toContain("none");
    expect(first).toContain("번역 없음");
  });
});
