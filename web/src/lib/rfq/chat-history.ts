/**
 * The chat conversation as the screen keeps it, and the part of it that goes back to the
 * backend (docs/design/chat.md §4). Earlier turns only tell what a question refers to; they
 * are never evidence. The limits are those of `ChatRequest` in the contract.
 */
import type { ChatResponse, ChatTurn, Locus } from "./contract";

export const MAX_HISTORY_TURNS = 6;
export const MAX_QUESTION_CHARS = 500;
export const MAX_ANSWER_CHARS = 4000;

/** `response` and `error` are both null while the answer is awaited. */
export type Turn = {
  question: string;
  response: ChatResponse | null;
  error: string | null;
  /** When the question was sent (ms). */
  startedAt: number;
};

type ChatData = ChatResponse["data"];
export type ShownStatement = ChatData["statements"][number];

/** The answer sentences on screen; the whole answer as one when the backend sent no statements. */
export function shownStatements(data: ChatData): ShownStatement[] {
  if (data.status !== "answered" || !data.answer) return [];
  if (data.statements.length > 0) return data.statements;
  const { text, citations } = data.answer.finding;
  return [{ text, citations }];
}

/** The citation the source viewer opens when an answer arrives. */
export function firstCitation(data: ChatData): Locus | null {
  return shownStatements(data).flatMap((s) => s.citations)[0] ?? null;
}

/** At most `max` UTF-16 units, never ending in half a surrogate pair (the backend rejects one). */
function cut(text: string, max: number): string {
  return text.length <= max ? text : text.slice(0, max).replace(/[\uD800-\uDBFF]$/, "");
}

function shownText(data: ChatData): string {
  const statements = shownStatements(data);
  return statements.length > 0 ? statements.map((s) => s.text).join("\n") : data.message;
}

/** The latest completed turns, oldest first. Failed and unanswered turns are left out. */
export function buildHistory(turns: readonly Turn[]): ChatTurn[] {
  return turns
    .flatMap((t) =>
      t.response
        ? [
            {
              question: cut(t.question, MAX_QUESTION_CHARS),
              answer: cut(shownText(t.response.data), MAX_ANSWER_CHARS),
            },
          ]
        : [],
    )
    .slice(-MAX_HISTORY_TURNS);
}
