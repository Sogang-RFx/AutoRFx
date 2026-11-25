/** What happens to a question before it is sent: empty, too long, or while an answer is still being made. */
import { describe, expect, it } from "vitest";
import { MAX_QUESTION_CHARS } from "@/lib/rfq/chat-history";
import { BUSY_NOTICE, TOO_LONG_NOTICE, checkQuestion } from "@/components/chat/question";

describe("checkQuestion", () => {
  it("passes a trimmed question", () => {
    expect(checkQuestion("  납기는?  ", false)).toEqual({ ok: true, question: "납기는?" });
  });

  it("drops an empty question without a notice", () => {
    expect(checkQuestion("   ", false)).toEqual({ ok: false, reason: "empty", notice: null });
  });

  it("accepts exactly the limit and refuses one more, saying what to do", () => {
    expect(checkQuestion("가".repeat(MAX_QUESTION_CHARS), false).ok).toBe(true);
    const over = checkQuestion("가".repeat(MAX_QUESTION_CHARS + 1), false);
    expect(over).toEqual({ ok: false, reason: "too_long", notice: TOO_LONG_NOTICE });
    expect(TOO_LONG_NOTICE).toContain(String(MAX_QUESTION_CHARS));
  });

  it("keeps the text for the input while an answer is pending, with a short notice", () => {
    expect(checkQuestion("납기는?", true)).toEqual({ ok: false, reason: "busy", notice: BUSY_NOTICE });
  });

  it("checks the length before the busy state", () => {
    expect(checkQuestion("가".repeat(MAX_QUESTION_CHARS + 1), true)).toMatchObject({ reason: "too_long" });
  });
});
