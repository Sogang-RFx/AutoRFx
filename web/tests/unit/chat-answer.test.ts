/**
 * What one chat answer shows (docs/design/chat.md §4): every sentence followed by its own
 * citations, conflicts in words and not by colour alone, and how the answer was made
 * (INV-3, INV-12). Texts are invented.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Answer } from "@/components/chat/answer";
import type { ChatResponse, Locus } from "@/lib/rfq/contract";

const locus = (page: number): Locus => ({
  source_doc: "d01",
  page_number: page,
  section_id: "1",
  paragraph_index: 1,
  sentence_index: 1,
  anchor: null,
});

type Data = ChatResponse["data"];

function render(data: Partial<Data>): string {
  const response: ChatResponse = {
    data: {
      status: "answered",
      mode: "generative",
      answer: {
        finding: {
          kind: "answer",
          departments: [],
          text: "합친 글",
          priority: null,
          red_flag_reason: null,
          citations: [locus(1), locus(2)],
        },
        citation_status: "verified",
        failed_citations: [],
      },
      statements: [
        { text: "첫 문장.", citations: [locus(1)] },
        { text: "둘째 문장.", citations: [locus(2)] },
      ],
      conflicts: [],
      message: "",
      suggestions: [],
      ...data,
    },
    provenance: { chat: "real" },
  };
  return renderToStaticMarkup(createElement(Answer, { response, target: null, onCite: () => {} }));
}

/** Every text in `parts` appears, in this order. */
function expectInOrder(html: string, parts: string[]) {
  const positions = parts.map((p) => html.indexOf(p));
  expect(positions).not.toContain(-1);
  expect(positions).toEqual([...positions].sort((a, b) => a - b));
}

describe("Answer", () => {
  it("puts each sentence's citations right after it", () => {
    const html = render({});
    expectInOrder(html, ["첫 문장.", "1페이지 1번째 문장", "둘째 문장.", "2페이지 1번째 문장"]);
    expect(html).not.toContain("합친 글");
  });

  it("shows the finding text when there are no statements", () => {
    expect(render({ statements: [] })).toContain("합친 글");
  });

  it("sets original sentences in the serif source style, and only those", () => {
    expect(render({ mode: "extractive" })).toContain("font-serif text-source");
    expect(render({ mode: "generative" })).not.toContain("font-serif");
    expect(render({ mode: "stored" })).not.toContain("font-serif");
  });

  it("says how the answer was made and keeps the server's message", () => {
    const html = render({ mode: "stored", message: "일부는 확인하지 못했습니다." });
    expect(html).toContain("분석 단계에서 저장된 결과");
    expect(html).toContain("일부는 확인하지 못했습니다.");
  });

  it("warns about conflicts in words, each with its citations", () => {
    const html = render({
      conflicts: [{ id: "c1", title: "상자 색이 다릅니다", detail: "파랑과 빨강", citations: [locus(7)] }],
    });
    expectInOrder(html, ["둘째 문장.", "문서마다 값이 다릅니다", "상자 색이 다릅니다", "파랑과 빨강", "7페이지 1번째 문장"]);
    expect(render({})).not.toContain("문서마다 값이 다릅니다");
  });

  it("shows the message for a guide and for a refusal", () => {
    for (const status of ["guide", "no_evidence", "restricted"] as const) {
      const html = render({ status, answer: null, statements: [], message: "안내 문구입니다." });
      expect(html).toContain(`data-testid="chat-${status}"`);
      expect(html).toContain("안내 문구입니다.");
    }
  });
});
