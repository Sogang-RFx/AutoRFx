/**
 * The conversation reducer of the chat screen: one question at a time, and a turn's progress as the stream
 * reports it. Texts are invented.
 */
import { describe, expect, it } from "vitest";
import {
  NO_PROGRESS,
  STAGE_NAMES,
  chatReducer,
  isWaiting,
  type ChatAction,
  type Turn,
} from "@/lib/rfq/chat-history";
import { CHAT_STAGES, type ChatResponse } from "@/lib/rfq/contract";

const response = {
  data: {
    status: "no_evidence",
    mode: "extractive",
    answer: null,
    statements: [],
    conflicts: [],
    message: "근거를 찾지 못했습니다.",
    suggestions: [],
  },
  provenance: { chat: "real" },
} as ChatResponse;

const run = (actions: ChatAction[], from: readonly Turn[] = []): Turn[] => actions.reduce(chatReducer, [...from]);
const asked = (question: string, at = 100): ChatAction => ({ type: "asked", question, at });
const stage = (name: (typeof CHAT_STAGES)[number], evidence?: number): ChatAction => ({
  type: "event",
  event: { type: "stage", stage: name, ...(evidence === undefined ? {} : { evidence }) },
});
const drafted = (n: number): ChatAction => ({ type: "event", event: { type: "progress", drafted: n } });

describe("asking", () => {
  it("adds a waiting turn with no progress", () => {
    const [turn] = run([asked("납기는?")]);
    expect(turn).toEqual({ question: "납기는?", response: null, error: null, startedAt: 100, progress: NO_PROGRESS });
    expect(isWaiting([turn])).toBe(true);
  });

  it("takes one question at a time: a second one while waiting is dropped", () => {
    const turns = run([asked("첫째"), asked("둘째")]);
    expect(turns.map((t) => t.question)).toEqual(["첫째"]);
  });

  it("takes the next question once the last turn has an answer or an error", () => {
    const answered = run([asked("a"), { type: "answered", response }, asked("b")]);
    expect(answered.map((t) => t.question)).toEqual(["a", "b"]);
    const failed = run([asked("a"), { type: "failed", message: "실패" }, asked("b")]);
    expect(failed.map((t) => t.question)).toEqual(["a", "b"]);
  });
});

describe("progress", () => {
  it("follows the stages and the evidence count", () => {
    const turns = run([asked("q"), stage("plan"), stage("search"), stage("write", 6)]);
    expect(turns[0].progress).toMatchObject({ stage: "write", evidence: 6, drafted: 0 });
  });

  it("counts every stream event, so the sphere moves only when something happened", () => {
    const turns = run([asked("q"), stage("plan"), stage("search"), stage("write", 6), drafted(1), drafted(2)]);
    expect(turns[0].progress.events).toBe(5);
    const withDone = run([{ type: "event", event: { type: "done", response } }], turns);
    expect(withDone[0].progress.events).toBe(5);
    expect(run([asked("q")])[0].progress.events).toBe(0);
  });

  it("starts the drafted count again when a search follows a write (the outage path)", () => {
    const turns = run([asked("q"), stage("write", 6), drafted(3), stage("search")]);
    expect(turns[0].progress).toMatchObject({ stage: "search", drafted: 0 });
    expect(run([stage("verify")], turns)[0].progress.drafted).toBe(0);
  });

  it("forgets the old stage, evidence and dot rows when the stream is asked again through /chat", () => {
    const turns = run([asked("q"), stage("write", 6), drafted(4), { type: "restarted" }]);
    expect(turns[0].progress).toMatchObject({ stage: null, evidence: null, drafted: 0 });
    expect(turns[0].progress.events).toBe(3); // write, progress, and the restart itself
    expect(isWaiting(turns)).toBe(true);
    // Only a waiting turn is touched.
    const finished = run([{ type: "answered", response }], turns);
    expect(run([{ type: "restarted" }], finished)).toEqual(finished);
  });

  it("counts the drafted statements and starts again at 0 when a stage begins a new draft", () => {
    const turns = run([asked("q"), stage("write", 6), drafted(1), drafted(3), stage("verify")]);
    expect(turns[0].progress.drafted).toBe(3);
    const again = run([stage("rewrite"), drafted(1)], turns);
    expect(again[0].progress).toMatchObject({ stage: "rewrite", evidence: 6, drafted: 1 });
    expect(run([stage("rewrite")], turns)[0].progress.drafted).toBe(0);
  });

  it("keeps the evidence count when a later stage does not repeat it", () => {
    const turns = run([asked("q"), stage("write", 4), stage("verify"), stage("rewrite")]);
    expect(turns[0].progress.evidence).toBe(4);
  });

  it("only touches the newest turn, and only while it waits", () => {
    const finished = run([asked("a"), stage("write", 2), { type: "answered", response }]);
    const late = run([stage("search"), drafted(5)], finished);
    expect(late).toEqual(finished);
    expect(run([stage("plan")], [])).toEqual([]);
  });

  it("leaves done and error to the request: the reducer does not read them", () => {
    const waiting = run([asked("q"), stage("plan")]);
    expect(run([{ type: "event", event: { type: "done", response } }], waiting)).toEqual(waiting);
    expect(
      run([{ type: "event", event: { type: "error", error: { code: "x", message: "y" } } }], waiting),
    ).toEqual(waiting);
  });

  it("does not change the turns it was given", () => {
    const before = run([asked("q")]);
    const frozen = JSON.stringify(before);
    run([stage("write", 3), drafted(2)], before);
    expect(JSON.stringify(before)).toBe(frozen);
  });
});

describe("the end of a turn", () => {
  it("keeps the progress with the answer", () => {
    const [turn] = run([asked("q"), stage("write", 2), drafted(2), { type: "answered", response }]);
    expect(turn.response).toBe(response);
    expect(turn.error).toBeNull();
    expect(turn.progress.drafted).toBe(2);
    expect(isWaiting([turn])).toBe(false);
  });

  it("records the error and no response", () => {
    const [turn] = run([asked("q"), { type: "failed", message: "연결하지 못했습니다." }]);
    expect(turn).toMatchObject({ response: null, error: "연결하지 못했습니다." });
  });

  it("ignores an answer or error when nothing waits", () => {
    expect(run([{ type: "answered", response }, { type: "failed", message: "x" }])).toEqual([]);
    const done = run([asked("q"), { type: "answered", response }]);
    expect(run([{ type: "failed", message: "늦은 오류" }], done)).toEqual(done);
  });
});

describe("stage names", () => {
  it("has a Korean name for every stage of the contract", () => {
    expect(Object.keys(STAGE_NAMES).sort()).toEqual([...CHAT_STAGES].sort());
    expect(STAGE_NAMES).toMatchObject({
      plan: "질문 이해",
      search: "근거 찾기",
      write: "답 쓰기",
      verify: "인용 확인",
      rewrite: "다시 쓰기",
    });
  });
});
