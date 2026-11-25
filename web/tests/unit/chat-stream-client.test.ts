/**
 * `rfqApi.chatStream`: the answer of the `done` event, the same checks as every response, and when it asks
 * once more through `/chat`. The voice calls are checked at the end. All values are invented.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatResponse, ChatStreamEvent } from "@/lib/rfq/contract";
import { API, apiError, json } from "./helpers";

type Provider = typeof import("@/lib/rfq/provider");

let provider: Provider;
let urls: string[];
let inits: RequestInit[];

const chatResponse = (provenance: Record<string, string> = { chat: "real" }): ChatResponse =>
  ({
    data: {
      status: "no_evidence",
      mode: "extractive",
      answer: null,
      statements: [],
      conflicts: [],
      message: "근거를 찾지 못했습니다.",
      suggestions: [],
    },
    provenance,
  }) as ChatResponse;

const plan: ChatStreamEvent = { type: "stage", stage: "plan" };
const frames = (...events: unknown[]) => events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
const stream = (text: string, status = 200, type = "text/event-stream") =>
  new Response(text, { status, headers: { "content-type": type } });

/** Routes by path: `/chat/stream` gets `onStream`, `/chat` answers normally and counts. */
function serve(onStream: () => Response | Promise<Response>) {
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    urls.push(url.replace(API, ""));
    inits.push(init);
    return url.endsWith("/chat/stream") ? onStream() : json(chatResponse());
  });
}

const asked = () => urls.filter((u) => u.endsWith("/chat")).length;

beforeEach(async () => {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_RFQ_API_BASE", API);
  provider = await import("@/lib/rfq/provider");
  urls = [];
  inits = [];
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("chatStream", () => {
  it("returns the response of the done event and reports every event", async () => {
    serve(() => stream(frames(plan, { type: "stage", stage: "write", evidence: 3 }, { type: "done", response: chatResponse() })));
    const seen: string[] = [];
    const response = await provider.rfqApi.chatStream("r1", "납기", [], { onEvent: (e) => seen.push(e.type) });
    expect(response.data.status).toBe("no_evidence");
    expect(seen).toEqual(["stage", "stage", "done"]);
    expect(urls).toEqual(["/rfqs/r1/chat/stream"]);
  });

  it("sends the question and the history like /chat, with the cookie, and asks for an event stream", async () => {
    serve(() => stream(frames({ type: "done", response: chatResponse() })));
    await provider.rfqApi.chatStream("r1", "납기", [{ question: "a", answer: "b" }]);
    expect(JSON.parse(inits[0].body as string)).toEqual({ question: "납기", history: [{ question: "a", answer: "b" }] });
    expect(inits[0].credentials).toBe("include");
    expect(new Headers(inits[0].headers).get("accept")).toBe("text/event-stream");
    expect(new Headers(inits[0].headers).get("content-type")).toBe("application/json");
  });

  it("asks again through /chat when the stream ends before done", async () => {
    serve(() => stream(frames(plan)));
    await provider.rfqApi.chatStream("r1", "납기");
    expect(asked()).toBe(1);
  });

  it("asks again when an event breaks the contract, or the reply is no stream", async () => {
    serve(() => stream(frames(plan, { type: "stage", stage: "dream" })));
    await provider.rfqApi.chatStream("r1", "납기");
    expect(asked()).toBe(1);
    serve(() => stream("{}", 200, "application/json"));
    await provider.rfqApi.chatStream("r1", "납기");
    expect(asked()).toBe(2);
  });

  it("tells the caller before it asks again, so the old progress can be dropped", async () => {
    serve(() => stream(frames(plan, { type: "stage", stage: "write", evidence: 3 })));
    const order: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url.replace(API, ""));
      if (url.endsWith("/chat/stream")) return stream(frames(plan, { type: "stage", stage: "write", evidence: 3 }));
      order.push("chat");
      return json(chatResponse());
    });
    await provider.rfqApi.chatStream("r1", "납기", [], {
      onEvent: (e) => order.push(e.type),
      onRestart: () => order.push("restart"),
    });
    expect(order).toEqual(["stage", "stage", "restart", "chat"]);
  });

  it("does not say restart when the stream delivers the answer, or on an error", async () => {
    const restarts = vi.fn();
    serve(() => stream(frames(plan, { type: "done", response: chatResponse() })));
    await provider.rfqApi.chatStream("r1", "납기", [], { onRestart: restarts });
    serve(() => stream(frames(plan, { type: "error", error: { code: "x", message: "y" } })));
    await provider.rfqApi.chatStream("r1", "납기", [], { onRestart: restarts }).catch(() => undefined);
    expect(restarts).not.toHaveBeenCalled();
  });

  it("asks again when the route does not exist (404, 405)", async () => {
    for (const status of [404, 405]) {
      serve(() => json({ detail: "Not Found" }, status));
      await provider.rfqApi.chatStream("r1", "납기");
    }
    expect(asked()).toBe(2);
  });

  it("asks only once: a second failure is the error of /chat", async () => {
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return url.endsWith("/chat/stream") ? stream(frames(plan)) : apiError(503, "chat_down", "지금은 답할 수 없습니다.");
    });
    await expect(provider.rfqApi.chatStream("r1", "납기")).rejects.toThrow("지금은 답할 수 없습니다.");
    expect(urls.filter((u) => u.endsWith("/chat"))).toHaveLength(1);
  });

  it("shows an error event as the error, without asking again", async () => {
    serve(() => stream(frames(plan, { type: "error", error: { code: "chat_failed", message: "답을 만들지 못했습니다." } })));
    await expect(provider.rfqApi.chatStream("r1", "납기")).rejects.toMatchObject({
      code: "chat_failed",
      message: "답을 만들지 못했습니다.",
    });
    expect(asked()).toBe(0);
  });

  it("shows an HTTP error before the stream as the error, without asking again", async () => {
    for (const [status, code] of [[422, "invalid_question"], [403, "forbidden"], [503, "down"]] as const) {
      serve(() => apiError(status, code, "거절"));
      await expect(provider.rfqApi.chatStream("r1", "납기")).rejects.toMatchObject({ status, code });
    }
    expect(asked()).toBe(0);
  });

  it("reports a session the server no longer accepts, once", async () => {
    const heard: string[] = [];
    provider.setAuthListener((e) => heard.push(e.code));
    serve(() => apiError(401, "unauthenticated"));
    await expect(provider.rfqApi.chatStream("r1", "납기")).rejects.toMatchObject({ status: 401 });
    expect(heard).toEqual(["unauthenticated"]);
    expect(asked()).toBe(0);
  });

  it("refuses an answer that is not real data, as /chat does", async () => {
    serve(() => stream(frames({ type: "done", response: chatResponse({ chat: "mock" }) })));
    await expect(provider.rfqApi.chatStream("r1", "납기")).rejects.toMatchObject({ code: "not_real_data" });
    expect(asked()).toBe(0);
  });

  it("never retries an abort", async () => {
    const controller = new AbortController();
    serve(() => {
      controller.abort();
      throw new DOMException("aborted", "AbortError");
    });
    await expect(provider.rfqApi.chatStream("r1", "납기", [], { signal: controller.signal })).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(asked()).toBe(0);
  });

  it("never retries an abort that comes while the stream is read", async () => {
    const controller = new AbortController();
    serve(
      () =>
        new Response(
          new ReadableStream<Uint8Array>({
            start(c) {
              c.enqueue(new TextEncoder().encode(frames(plan)));
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        ),
    );
    const pending = provider.rfqApi.chatStream("r1", "납기", [], {
      signal: controller.signal,
      onEvent: () => controller.abort(),
    });
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(asked()).toBe(0);
  });
});

describe("voice calls", () => {
  it("transcribe sends the recording as the multipart field audio", async () => {
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      urls.push(url.replace(API, ""));
      inits.push(init);
      return json({ data: { text: "납기는 언제인가요" }, provenance: { chat: "real" } });
    });
    const result = await provider.rfqApi.transcribe(new Blob(["x"], { type: "audio/webm;codecs=opus" }));
    expect(result.data.text).toBe("납기는 언제인가요");
    expect(urls).toEqual(["/voice/transcriptions"]);
    const form = inits[0].body as FormData;
    expect([...form.keys()]).toEqual(["audio"]);
    expect((form.get("audio") as File).name).toBe("question.webm");
    expect(inits[0].credentials).toBe("include");
  });

  it("speak sends the text and returns the MP3 bytes; a JSON body in place of audio is refused", async () => {
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      urls.push(url.replace(API, ""));
      inits.push(init);
      return new Response(new Uint8Array([1, 2, 3]), { headers: { "content-type": "audio/mpeg" } });
    });
    expect([...(await provider.rfqApi.speak("읽을 문장"))]).toEqual([1, 2, 3]);
    expect(urls).toEqual(["/voice/speech"]);
    expect(JSON.parse(inits[0].body as string)).toEqual({ text: "읽을 문장" });

    vi.stubGlobal("fetch", async () => json({}));
    await expect(provider.rfqApi.speak("읽을 문장")).rejects.toMatchObject({ code: "contract_violation" });
  });

  it("reports the service error with the server's words", async () => {
    vi.stubGlobal("fetch", async () => apiError(503, "voice_unavailable", "음성 기능을 쓸 수 없습니다."));
    await expect(provider.rfqApi.speak("문장")).rejects.toMatchObject({ status: 503, code: "voice_unavailable" });
    await expect(provider.rfqApi.transcribe(new Blob(["x"], { type: "audio/mp4" }))).rejects.toMatchObject({
      status: 503,
    });
  });
});
