/**
 * What is read aloud: the verified statements, or the message when there is no answer; cut at a sentence
 * end; never citation positions or file names. All texts are invented.
 */
import { describe, expect, it } from "vitest";
import type { ChatResponse, Locus } from "@/lib/rfq/contract";
import { speakable } from "@/lib/voice/speakable";

const locus: Locus = {
  source_doc: "d01",
  page_number: 3,
  section_id: "2.1",
  paragraph_index: 1,
  sentence_index: 4,
  anchor: null,
};

type Data = ChatResponse["data"];

function reply(data: Partial<Data>): ChatResponse {
  return {
    data: {
      status: "no_evidence",
      mode: "generative",
      answer: null,
      statements: [],
      conflicts: [],
      message: "근거를 찾지 못했습니다.",
      suggestions: [],
      ...data,
    },
    provenance: { chat: "real" },
  };
}

const answered = (...texts: string[]): ChatResponse =>
  reply({
    status: "answered",
    answer: {
      finding: { kind: "answer", text: texts.join("\n"), citations: [locus] },
      citation_status: "verified",
    } as unknown as Data["answer"],
    statements: texts.map((text) => ({ text, citations: [locus] })),
  });

describe("speakable", () => {
  it("reads the verified statements, one after the other", () => {
    expect(speakable(answered("납기는 계약 후 30일입니다.", "검수는 인도 후 7일 안에 합니다."), 1200)).toBe(
      "납기는 계약 후 30일입니다.\n검수는 인도 후 7일 안에 합니다.",
    );
  });

  it("reads statements only when the citations were verified", () => {
    const res = answered("납기는 30일입니다.");
    (res.data.answer as { citation_status: string }).citation_status = "unverified";
    expect(speakable(res, 1200)).toBe("");
    (res.data.answer as { citation_status: string }).citation_status = "verified";
    expect(speakable(res, 1200)).toBe("납기는 30일입니다.");
  });

  it("does not fall back to the message when an unverified answer exists", () => {
    const res = answered("납기는 30일입니다.");
    (res.data.answer as { citation_status: string }).citation_status = "unverified";
    res.data.message = "원문 문장 그대로";
    expect(speakable(res, 1200)).toBe("");
  });

  it("reads the message when there is no answer", () => {
    expect(speakable(reply({ message: "근거를 찾지 못했습니다." }), 1200)).toBe("근거를 찾지 못했습니다.");
    expect(speakable(reply({ status: "guide", message: "이렇게 물어보세요." }), 1200)).toBe("이렇게 물어보세요.");
  });

  it("does not read the mode note, the suggestions or the conflicts", () => {
    const res = answered("납기는 30일입니다.");
    res.data.suggestions = ["다른 질문"];
    res.data.message = "원문 문장 그대로";
    res.data.conflicts = [{ id: "c1", title: "문서마다 다른 값", detail: "상세", citations: [locus] }];
    expect(speakable(res, 1200)).toBe("납기는 30일입니다.");
  });

  it("leaves out citation positions and file names", () => {
    const text = "납기는 30일입니다 [p.3 ¶2] (p. 4) (페이지 5) (rfq_main.pdf) 입니다. 가격표.xlsx 를 보세요.";
    const read = speakable(answered(text), 1200);
    expect(read).not.toMatch(/\[|\]|p\.|\.pdf|\.xlsx|페이지/);
    expect(read).toContain("납기는 30일입니다");
    expect(read).not.toMatch(/ {2}/);
  });

  it("keeps whole statements that fit and stops before the one that does not", () => {
    const res = answered("첫 문장입니다.", "둘째 문장입니다.", "셋째 문장입니다.");
    expect(speakable(res, 20)).toBe("첫 문장입니다.\n둘째 문장입니다.");
    expect(speakable(res, 100)).toBe("첫 문장입니다.\n둘째 문장입니다.\n셋째 문장입니다.");
  });

  it("cuts a long first statement at a sentence end", () => {
    const long = "가나다라마바사 아자차카타파하. 두 번째 문장은 이어집니다. 세 번째는 아주 길게 이어지는 문장입니다.";
    const read = speakable(answered(long), 40);
    expect(read).toBe("가나다라마바사 아자차카타파하. 두 번째 문장은 이어집니다.");
    expect(read.length).toBeLessThanOrEqual(40);
  });

  it("falls back to a space, then to the limit, when a sentence has no end", () => {
    expect(speakable(answered("하나 둘 셋 넷 다섯 여섯 일곱"), 10)).toBe("하나 둘 셋 넷");
    expect(speakable(answered("가나다라마바사아자차"), 4)).toBe("가나다라");
  });

  it("never ends in half a surrogate pair", () => {
    const read = speakable(answered("😀😀😀😀😀"), 3);
    expect(read).toBe("😀");
  });

  it("returns an empty text when there is nothing to read", () => {
    expect(speakable(reply({ message: "  " }), 1200)).toBe("");
    expect(speakable(answered("[p.3]"), 1200)).toBe("");
    expect(speakable(answered("읽을 문장입니다."), 0)).toBe("");
  });

  it("reads the whole answer as one when the backend sent no statements", () => {
    const res = answered("단일 답변입니다.");
    res.data.statements = [];
    expect(speakable(res, 1200)).toBe("단일 답변입니다.");
  });
});
