/**
 * Reader of the chat stream (`POST /rfqs/{id}/chat/stream`, docs/design/immersive-ui.md §5).
 * Pure and incremental: text or bytes in, validated `ChatStreamEvent`s out. No fetch and no React here;
 * the provider owns the request and decides what a broken stream means.
 */
import { ChatStreamEvent } from "./contract";

/** The stream sent something that is not an event of the contract. The caller asks once through `/chat`. */
export class ChatStreamContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ChatStreamContractError";
  }
}

/** One line ends at LF, CRLF or a lone CR. */
const LINE_END = /\r\n|\n|\r/;
const DATA_PREFIX = "data:";

export interface SseParser {
  /** Data payloads of the events that this chunk finished. */
  push(chunk: string | Uint8Array): string[];
  /** The stream ended: an event that was not closed by a blank line is dropped, as the SSE rules say. */
  finish(): string[];
}

/** Value of an SSE field line: one space after the colon is not part of it. */
function valueOf(line: string, prefix: string): string {
  const value = line.slice(prefix.length);
  return value.startsWith(" ") ? value.slice(1) : value;
}

export function createSseParser(): SseParser {
  const decoder = new TextDecoder();
  let buffer = "";
  let data: string[] = [];

  /** Takes complete lines out of the buffer; a trailing CR waits, since an LF may follow in the next chunk. */
  function drain(): string[] {
    const events: string[] = [];
    for (;;) {
      const match = LINE_END.exec(buffer);
      if (!match) break;
      const end = match.index + match[0].length;
      if (match[0] === "\r" && end === buffer.length) break;
      const line = buffer.slice(0, match.index);
      buffer = buffer.slice(end);
      if (line === "") {
        if (data.length > 0) events.push(data.join("\n"));
        data = [];
      } else if (line.startsWith(DATA_PREFIX)) {
        data.push(valueOf(line, DATA_PREFIX));
      }
      // Comments (":") and the fields event, id and retry carry nothing the screen uses.
    }
    return events;
  }

  return {
    push(chunk) {
      buffer += typeof chunk === "string" ? chunk : decoder.decode(chunk, { stream: true });
      return drain();
    },
    finish() {
      buffer = "";
      data = [];
      return [];
    },
  };
}

export interface ChatStreamReader {
  /** Events finished by this chunk. Throws `ChatStreamContractError` for data that breaks the contract. */
  push(chunk: string | Uint8Array): ChatStreamEvent[];
  /** True once `done` or `error` was read: nothing after it is looked at. */
  readonly isEnded: boolean;
}

function parseEvent(payload: string): ChatStreamEvent {
  let json: unknown;
  try {
    json = JSON.parse(payload);
  } catch {
    throw new ChatStreamContractError("스트림의 이벤트가 JSON이 아닙니다.");
  }
  const checked = ChatStreamEvent.safeParse(json);
  if (!checked.success) throw new ChatStreamContractError("스트림의 이벤트가 계약(contract/)과 맞지 않습니다.");
  return checked.data;
}

export function createChatStreamReader(): ChatStreamReader {
  const sse = createSseParser();
  let isEnded = false;
  return {
    get isEnded() {
      return isEnded;
    },
    push(chunk) {
      if (isEnded) return [];
      const events: ChatStreamEvent[] = [];
      for (const payload of sse.push(chunk)) {
        const event = parseEvent(payload);
        events.push(event);
        if (event.type === "done" || event.type === "error") {
          isEnded = true;
          break;
        }
      }
      return events;
    },
  };
}

function abortError(): DOMException {
  return new DOMException("The operation was aborted.", "AbortError");
}

/**
 * Reads a response body to its end and gives every event to `onEvent`. Returns the terminal event (`done` or
 * `error`), or null when the body ended without one. An abort rejects with an AbortError. A callback that
 * throws is ignored: the view failing to show progress must not cost the answer.
 */
export async function readChatStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<ChatStreamEvent | null> {
  const reader = body.getReader();
  const parser = createChatStreamReader();
  try {
    for (;;) {
      if (signal?.aborted) throw abortError();
      const { done, value } = await reader.read();
      if (signal?.aborted) throw abortError();
      if (done) return null;
      for (const event of parser.push(value)) {
        try {
          onEvent(event);
        } catch {
          // Progress display only; see above.
        }
        if (event.type === "done" || event.type === "error") return event;
      }
    }
  } finally {
    // Frees the connection when we stop early; a body that is already closed rejects, which does not matter.
    void reader.cancel().catch(() => undefined);
  }
}
