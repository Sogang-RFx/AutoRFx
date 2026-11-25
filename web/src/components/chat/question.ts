/** What happens to a question before it is sent: empty, too long (a transcript can be), or an answer is pending. */
import { MAX_QUESTION_CHARS } from "@/lib/rfq/chat-history";

export const TOO_LONG_NOTICE = `질문이 ${MAX_QUESTION_CHARS}자를 넘어 보내지 않았습니다. 입력 칸의 글을 ${MAX_QUESTION_CHARS}자 안으로 줄여서 다시 보내 주세요.`;
export const BUSY_NOTICE = "지금 답을 만드는 중이라 말씀하신 질문을 입력 칸에 넣어 두었습니다. 답이 오면 보내 주세요.";

export type QuestionCheck =
  | { ok: true; question: string }
  | { ok: false; reason: "empty" | "too_long" | "busy"; notice: string | null };

export function checkQuestion(text: string, isBusy: boolean): QuestionCheck {
  const question = text.trim();
  if (!question) return { ok: false, reason: "empty", notice: null };
  if (question.length > MAX_QUESTION_CHARS) return { ok: false, reason: "too_long", notice: TOO_LONG_NOTICE };
  if (isBusy) return { ok: false, reason: "busy", notice: BUSY_NOTICE };
  return { ok: true, question };
}
