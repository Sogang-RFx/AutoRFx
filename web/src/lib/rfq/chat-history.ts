/**
 * The chat conversation as the screen keeps it, and the part of it that goes back to the
 * backend (docs/design/chat.md §4). Earlier turns only tell what a question refers to; they
 * are never evidence. The limits are those of `ChatRequest` in the contract.
 */
import type { ChatResponse, ChatStage, ChatStreamEvent, ChatTurn, Locus } from "./contract";

export const MAX_HISTORY_TURNS = 6;
export const MAX_QUESTION_CHARS = 500;
export const MAX_ANSWER_CHARS = 4000;

/** What the stream has reported of a turn so far. No draft text: the stream does not carry any. */
export type TurnProgress = {
  stage: ChatStage | null;
  /** Evidence sentences the writer was given; null until the `write` stage says so. */
  evidence: number | null;
  /** Statements written so far in the current draft. */
  drafted: number;
  /** Stream events seen so far: the sphere ripples once for each, so it moves only when something happened. */
  events: number;
};

export const NO_PROGRESS: TurnProgress = { stage: null, evidence: null, drafted: 0, events: 0 };

/** The stage names shown while an answer is made. */
export const STAGE_NAMES: Record<ChatStage, string> = {
  plan: "질문 이해",
  search: "근거 찾기",
  write: "답 쓰기",
  verify: "인용 확인",
  rewrite: "다시 쓰기",
};

/** `response` and `error` are both null while the answer is awaited. */
export type Turn = {
  question: string;
  response: ChatResponse | null;
  error: string | null;
  /** When the question was sent (ms). */
  startedAt: number;
  progress: TurnProgress;
};

export const isTurnWaiting = (t: Turn): boolean => t.response === null && t.error === null;

/** True while the newest turn has neither an answer nor an error: one question at a time. */
export function isWaiting(turns: readonly Turn[]): boolean {
  const last = turns.at(-1);
  return last !== undefined && isTurnWaiting(last);
}

export type ChatAction =
  | { type: "asked"; question: string; at: number }
  /** An event of the stream; only `stage` and `progress` matter here (`done` and `error` end the request). */
  | { type: "event"; event: ChatStreamEvent }
  /** The stream died and the plain request is asked instead: what the stream showed no longer holds. */
  | { type: "restarted" }
  | { type: "answered"; response: ChatResponse }
  | { type: "failed"; message: string };

function progressAfter(p: TurnProgress, event: ChatStreamEvent): TurnProgress {
  if (event.type === "progress") return { ...p, drafted: event.drafted, events: p.events + 1 };
  if (event.type !== "stage") return p;
  // `write` and `rewrite` begin a draft, and a `search` after a `write` is the outage path: the count starts again.
  const startsDraft = event.stage === "write" || event.stage === "rewrite" || event.stage === "search";
  return {
    stage: event.stage,
    evidence: event.evidence ?? p.evidence,
    drafted: startsDraft ? 0 : p.drafted,
    events: p.events + 1,
  };
}

/** Changes the newest turn while it waits; anything for a finished turn (a late event) is dropped. */
function updateWaiting(turns: readonly Turn[], change: (t: Turn) => Turn): Turn[] {
  const last = turns.at(-1);
  if (!last || !isTurnWaiting(last)) return [...turns];
  return [...turns.slice(0, -1), change(last)];
}

export function chatReducer(turns: readonly Turn[], action: ChatAction): Turn[] {
  switch (action.type) {
    case "asked":
      if (isWaiting(turns)) return [...turns];
      return [
        ...turns,
        { question: action.question, response: null, error: null, startedAt: action.at, progress: NO_PROGRESS },
      ];
    case "event":
      return updateWaiting(turns, (t) => ({ ...t, progress: progressAfter(t.progress, action.event) }));
    case "restarted":
      return updateWaiting(turns, (t) => ({ ...t, progress: { ...NO_PROGRESS, events: t.progress.events + 1 } }));
    case "answered":
      return updateWaiting(turns, (t) => ({ ...t, response: action.response }));
    case "failed":
      return updateWaiting(turns, (t) => ({ ...t, error: action.message }));
  }
}

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
