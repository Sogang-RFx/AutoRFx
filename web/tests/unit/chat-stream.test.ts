/**
 * The incremental SSE reader of the chat stream (docs/design/immersive-ui.md §5): events split across
 * chunks, several in one chunk, comments, CRLF, and events that break the contract. Invented values only.
 */
import { describe, expect, it } from "vitest";
import {
  ChatStreamContractError,
  createChatStreamReader,
  createSseParser,
  readChatStream,
} from "@/lib/rfq/chat-stream";
import type { ChatStreamEvent } from "@/lib/rfq/contract";

const plan = { type: "stage", stage: "plan" } as const;
const write = { type: "stage", stage: "write", evidence: 4 } as const;
const progress = { type: "progress", drafted: 2 } as const;
const failure = { type: "error", error: { code: "chat_failed", message: "답을 만들지 못했습니다." } } as const;
const done = {
  type: "done",
  response: {
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
  },
} as const;

const frame = (event: unknown, eol = "\n") => `data: ${JSON.stringify(event)}${eol}${eol}`;

describe("createSseParser", () => {
  it("returns the data of each finished event", () => {
    const parser = createSseParser();
    expect(parser.push("data: one\n\ndata: two\n\n")).toEqual(["one", "two"]);
  });

  it("waits for the blank line that ends an event", () => {
    const parser = createSseParser();
    expect(parser.push("data: one\n")).toEqual([]);
    expect(parser.push("\n")).toEqual(["one"]);
  });

  it("joins several data lines with a newline", () => {
    const parser = createSseParser();
    expect(parser.push("data: a\ndata: b\n\n")).toEqual(["a\nb"]);
  });

  it("handles a line cut in the middle and a value without the space after the colon", () => {
    const parser = createSseParser();
    expect(parser.push("dat")).toEqual([]);
    expect(parser.push("a:abc")).toEqual([]);
    expect(parser.push("def\n\n")).toEqual(["abcdef"]);
  });

  it("handles CRLF, a lone CR, and a CRLF cut between the two characters", () => {
    expect(createSseParser().push("data: a\r\n\r\ndata: b\r\rx")).toEqual(["a", "b"]);
    const parser = createSseParser();
    expect(parser.push("data: a\r")).toEqual([]);
    expect(parser.push("\n\r")).toEqual([]);
    expect(parser.push("\n")).toEqual(["a"]);
  });

  it("ignores comments and fields it does not use", () => {
    const parser = createSseParser();
    expect(parser.push(": keep-alive\n\nevent: x\nid: 7\nretry: 100\ndata: ok\n: note\n\n")).toEqual(["ok"]);
  });

  it("does not report an event that has no data, and drops one left open at the end", () => {
    const parser = createSseParser();
    expect(parser.push("event: ping\n\n")).toEqual([]);
    expect(parser.push("data: never closed")).toEqual([]);
    expect(parser.finish()).toEqual([]);
  });

  it("keeps a multibyte character that is cut between two chunks of bytes", () => {
    const bytes = new TextEncoder().encode("data: 납기\n\n");
    const parser = createSseParser();
    expect([...parser.push(bytes.slice(0, 8)), ...parser.push(bytes.slice(8))]).toEqual(["납기"]);
  });
});

describe("createChatStreamReader", () => {
  it("returns validated events in order, however the stream was cut", () => {
    const text = [plan, write, progress, done].map((e) => frame(e)).join("");
    for (const size of [1, 3, 17, text.length]) {
      const reader = createChatStreamReader();
      const seen: ChatStreamEvent[] = [];
      for (let i = 0; i < text.length; i += size) seen.push(...reader.push(text.slice(i, i + size)));
      expect(seen.map((e) => e.type), `chunks of ${size}`).toEqual(["stage", "stage", "progress", "done"]);
    }
  });

  it("returns several events that arrive in one chunk", () => {
    const reader = createChatStreamReader();
    expect(reader.push(frame(plan) + frame(write, "\r\n")).map((e) => e.type)).toEqual(["stage", "stage"]);
  });

  it("refuses data that is not JSON, and a shape the contract does not have", () => {
    expect(() => createChatStreamReader().push("data: {broken\n\n")).toThrow(ChatStreamContractError);
    expect(() => createChatStreamReader().push(frame({ type: "stage", stage: "dream" }))).toThrow(
      ChatStreamContractError,
    );
    expect(() => createChatStreamReader().push(frame({ type: "progress", drafted: -1 }))).toThrow(
      ChatStreamContractError,
    );
    expect(() => createChatStreamReader().push(frame({ type: "unknown" }))).toThrow(ChatStreamContractError);
  });

  it("stops at the first done or error: nothing after it is read", () => {
    const afterDone = createChatStreamReader();
    expect(afterDone.push(frame(done) + frame(progress)).map((e) => e.type)).toEqual(["done"]);
    expect(afterDone.push(frame(plan))).toEqual([]);
    expect(afterDone.isEnded).toBe(true);

    const afterError = createChatStreamReader();
    expect(afterError.push(frame(failure) + "data: {broken\n\n").map((e) => e.type)).toEqual(["error"]);
  });
});

const streamOf = (...chunks: (string | Uint8Array)[]) =>
  new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const c of chunks) controller.enqueue(typeof c === "string" ? encoder.encode(c) : c);
      controller.close();
    },
  });

describe("readChatStream", () => {
  it("hands every event to the callback and returns the last one", async () => {
    const seen: string[] = [];
    const body = streamOf(frame(plan), frame(write).slice(0, 10), frame(write).slice(10), frame(done));
    const last = await readChatStream(body, (e) => seen.push(e.type));
    expect(seen).toEqual(["stage", "stage", "done"]);
    expect(last?.type).toBe("done");
  });

  it("returns null when the stream ends before done or error", async () => {
    const last = await readChatStream(streamOf(frame(plan), frame(progress)), () => undefined);
    expect(last).toBeNull();
  });

  it("keeps reading when the callback throws: progress is not worth losing the answer for", async () => {
    const last = await readChatStream(streamOf(frame(plan), frame(done)), () => {
      throw new Error("view broke");
    });
    expect(last?.type).toBe("done");
  });

  it("passes on a contract violation", async () => {
    await expect(readChatStream(streamOf("data: nope\n\n"), () => undefined)).rejects.toBeInstanceOf(
      ChatStreamContractError,
    );
  });

  it("stops reading when the signal is aborted", async () => {
    const controller = new AbortController();
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode(frame(plan)));
      },
    });
    const seen: string[] = [];
    const reading = readChatStream(
      body,
      (e) => {
        seen.push(e.type);
        controller.abort();
      },
      controller.signal,
    );
    await expect(reading).rejects.toMatchObject({ name: "AbortError" });
    expect(seen).toEqual(["stage"]);
  });
});
