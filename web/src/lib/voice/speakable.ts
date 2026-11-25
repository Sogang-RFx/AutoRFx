/**
 * What the screen reads aloud (docs/design/immersive-ui.md §6): the verified statements that are on screen,
 * or the message when there is no answer. Citation positions and file names are left out, and the text is
 * cut at a sentence end so the voice never stops in the middle of one.
 */
import { shownStatements } from "@/lib/rfq/chat-history";
import type { ChatResponse } from "@/lib/rfq/contract";

const SENTENCE_END = /[.!?。…](?=\s|$)/g;

/** `[p.3 ¶2]`, `(p. 4)`, `(페이지 5)`, `(3쪽)` and names of files such as `a.pdf` or `(a.xlsx)`. */
const CITATION_MARKS = [
  /\[[^\]]*\]/g,
  /\(\s*(?:p\.?|pp\.?|page|페이지|쪽)\s*\d[^)]*\)/gi,
  /\(\s*\d+\s*(?:쪽|페이지)[^)]*\)/g,
  /\(?[\w가-힣.-]+\.(?:pdf|docx?|xlsx?|pptx?|hwpx?|txt|csv)\)?/gi,
];

function clean(text: string): string {
  const without = CITATION_MARKS.reduce((t, pattern) => t.replace(pattern, " "), text);
  return without.replace(/[ \t]+/g, " ").replace(/ +([.,!?])/g, "$1").trim();
}

/** At most `max` UTF-16 units, never ending in half a surrogate pair. */
function hardCut(text: string, max: number): string {
  return text.slice(0, max).replace(/[\uD800-\uDBFF]$/, "").trimEnd();
}

/** The longest part of `text` that ends a sentence within `max`; else one that ends at a space; else a hard cut. */
function cutAtSentence(text: string, max: number): string {
  if (text.length <= max) return text;
  let end = -1;
  for (const m of text.matchAll(SENTENCE_END)) {
    const stop = (m.index ?? 0) + m[0].length;
    if (stop > max) break;
    end = stop;
  }
  if (end > 0) return text.slice(0, end).trimEnd();
  const space = text.lastIndexOf(" ", max);
  return space > 0 ? text.slice(0, space).trimEnd() : hardCut(text, max);
}

export function speakable(response: ChatResponse, maxChars: number): string {
  if (maxChars <= 0) return "";
  const { answer, message } = response.data;
  const shown = shownStatements(response.data);
  // Statements are read only when their citations were verified (CITE-01). With no answer the message is a sentence
  // of the server, not of a document; an answer's message is a note about how it was made.
  if (shown.length > 0 && answer?.citation_status !== "verified") return "";
  const texts = shown.length > 0 ? shown.map((s) => s.text) : [message];
  const parts = texts.map(clean).filter((t) => t !== "");
  const kept: string[] = [];
  let used = 0;
  for (const part of parts) {
    const room = maxChars - used - (kept.length > 0 ? 1 : 0);
    if (part.length <= room) {
      kept.push(part);
      used += part.length + (kept.length > 1 ? 1 : 0);
      continue;
    }
    // The first statement alone may be longer than the limit; later ones are left out whole.
    if (kept.length === 0) kept.push(cutAtSentence(part, room));
    break;
  }
  return kept.join("\n").trim();
}
