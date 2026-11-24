/**
 * What the chat screen sends as `history` (docs/design/chat.md §4) and which sentences it
 * shows. History only resolves what a question refers to; it is never evidence. Texts are invented.
 */
import { describe, expect, it } from "vitest";
import {
  MAX_ANSWER_CHARS,
  MAX_HISTORY_TURNS,
  MAX_QUESTION_CHARS,
  buildHistory,
  firstCitation,
  shownStatements,
  type Turn,
} from "@/lib/rfq/chat-history";
import { ChatRequest, type ChatResponse, type Locus } from "@/lib/rfq/contract";

const locus = (sentence: number): Locus => ({
  source_doc: "d01",
  page_number: 1,
  section_id: "1",
  paragraph_index: 1,
  sentence_index: sentence,
  anchor: null,
});

type Data = ChatResponse["data"];

function response(data: Partial<Data>): ChatResponse {
  return {
    data: {
      status: "no_evidence",
      mode: "extractive",
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

function answered(text: string, statements: Data["statements"] = []): ChatResponse {
  return response({
    status: "answered",
    mode: "generative",
    answer: {
      finding: {
        kind: "answer",
        departments: [],
        text,
        priority: null,
        red_flag_reason: null,
        citations: [locus(9)],
      },
      citation_status: "verified",
      failed_citations: [],
    },
    statements,
    message: "안내",
  });
}

const turn = (question: string, res: ChatResponse | null, error: string | null = null): Turn => ({
  question,
  response: res,
  error,
  startedAt: 0,
});

describe("buildHistory", () => {
  it("keeps the question with the answer text, or the message when there is no answer", () => {
    expect(
      buildHistory([
        turn("상자는 무슨 색인가?", answered("상자는 파란색입니다.")),
        turn("뚜껑은?", response({ message: "근거를 찾지 못했습니다." })),
        turn("뭘 물어볼 수 있어?", response({ status: "guide", message: "이렇게 물어보세요." })),
      ]),
    ).toEqual([
      { question: "상자는 무슨 색인가?", answer: "상자는 파란색입니다." },
      { question: "뚜껑은?", answer: "근거를 찾지 못했습니다." },
      { question: "뭘 물어볼 수 있어?", answer: "이렇게 물어보세요." },
    ]);
  });

  it("uses the statements shown on screen", () => {
    const res = answered("합친 글", [
      { text: "첫 문장.", citations: [locus(1)] },
      { text: "둘째 문장.", citations: [locus(2)] },
    ]);
    expect(buildHistory([turn("질문", res)])[0].answer).toBe("첫 문장.\n둘째 문장.");
  });

  it("leaves out failed and unanswered turns", () => {
    const turns = [turn("실패", null, "오류"), turn("기다림", null), turn("끝남", answered("답"))];
    expect(buildHistory(turns).map((t) => t.question)).toEqual(["끝남"]);
  });

  it("keeps the latest six completed turns, oldest first", () => {
    const turns = Array.from({ length: 9 }, (_, i) => turn(`질문 ${i}`, answered(`답 ${i}`)));
    const withFailure = [...turns, turn("실패", null, "오류")];
    expect(buildHistory(withFailure).map((t) => t.question)).toEqual(
      [3, 4, 5, 6, 7, 8].map((i) => `질문 ${i}`),
    );
    expect(buildHistory([])).toEqual([]);
  });

  it("always fits the contract limits", () => {
    const turns = Array.from({ length: 8 }, () => turn("가".repeat(900), answered("나".repeat(9000))));
    const history = buildHistory(turns);
    expect(history).toHaveLength(MAX_HISTORY_TURNS);
    expect(history[0].question).toHaveLength(MAX_QUESTION_CHARS);
    expect(history[0].answer).toHaveLength(MAX_ANSWER_CHARS);
    expect(ChatRequest.safeParse({ question: "다음은?", history }).success).toBe(true);
  });

  it("uses the whole limit the contract allows", () => {
    const over = (q: number, a: number, n: number) =>
      ChatRequest.safeParse({
        question: "다음은?",
        history: Array.from({ length: n }, () => ({ question: "가".repeat(q), answer: "나".repeat(a) })),
      }).success;
    expect(over(MAX_QUESTION_CHARS, MAX_ANSWER_CHARS, MAX_HISTORY_TURNS)).toBe(true);
    expect(over(MAX_QUESTION_CHARS + 1, MAX_ANSWER_CHARS, MAX_HISTORY_TURNS)).toBe(false);
    expect(over(MAX_QUESTION_CHARS, MAX_ANSWER_CHARS + 1, MAX_HISTORY_TURNS)).toBe(false);
    expect(over(MAX_QUESTION_CHARS, MAX_ANSWER_CHARS, MAX_HISTORY_TURNS + 1)).toBe(false);
  });

  it("does not leave half a character at the cut", () => {
    const text = `${"가".repeat(MAX_ANSWER_CHARS - 1)}😀`;
    expect(buildHistory([turn("질문", answered(text))])[0].answer).toBe("가".repeat(MAX_ANSWER_CHARS - 1));
  });
});

describe("shownStatements", () => {
  it("falls back to the finding when there are no statements", () => {
    expect(shownStatements(answered("상자는 파란색입니다.").data)).toEqual([
      { text: "상자는 파란색입니다.", citations: [locus(9)] },
    ]);
  });

  it("shows nothing without an answer", () => {
    expect(shownStatements(response({}).data)).toEqual([]);
  });
});

describe("firstCitation", () => {
  it("is the first citation of the first shown sentence", () => {
    expect(firstCitation(answered("글", [{ text: "첫 문장.", citations: [locus(3), locus(4)] }]).data)).toEqual(locus(3));
    expect(firstCitation(answered("글").data)).toEqual(locus(9));
    expect(firstCitation(response({}).data)).toBeNull();
  });
});
