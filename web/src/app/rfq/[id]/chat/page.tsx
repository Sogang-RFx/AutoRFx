"use client";
/**
 * RFQ chatbot (TASKS S016, SPEC §14, US6). Answers come from the backend and
 * quote the source; with no evidence it does not answer. Citations open the
 * source viewer. The conversation resets when the account changes.
 */
import { useParams } from "next/navigation";
import { useState } from "react";
import { SourceViewer } from "@/components/source-viewer/source-viewer";
import { IconArrowRight, IconLock } from "@/components/ui/icons";
import { Button, CitationChip, ErrorState, ProvenanceBadge, cx, locusKey, sameLocus } from "@/components/ui/primitives";
import type { ChatResponse, Locus } from "@/lib/rfq/contract";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { scopeName, seesWholeSource } from "@/lib/rfq/scope";
import { useViewer } from "@/lib/rfq/session";

type Turn = { question: string; response: ChatResponse | null; error: string | null };

/** First suggestions before any answer: general RFQ topics. */
const STARTERS = ["인도조건", "납기", "포장"];

const MODE_NOTE: Record<ChatResponse["data"]["mode"], string> = {
  scripted: "준비된 문답",
  extractive: "원문 문장 그대로 · LLM 미연결로 요약하지 않음",
  generative: "LLM이 원문을 근거로 쓴 답",
};

export default function ChatPage() {
  const { id } = useParams<{ id: string }>();
  const viewer = useViewer();
  return <Chat key={viewer.user_id} rfqId={decodeURIComponent(id)} />;
}

function Chat({ rfqId }: { rfqId: string }) {
  const viewer = useViewer();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [target, setTarget] = useState<Locus | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  const lastResponse = [...turns].reverse().find((t) => t.response)?.response ?? null;
  const suggested = lastResponse?.data.suggestions ?? [];
  const lastSuggestions = suggested.length > 0 ? suggested : STARTERS;
  // The badge follows the server's provenance (INV-10: never decided by the screen).
  const headerProvenance = lastResponse?.provenance;

  async function ask(question: string) {
    const q = question.trim();
    if (!q || isBusy) return;
    setDraft("");
    setIsBusy(true);
    setTurns((t) => [...t, { question: q, response: null, error: null }]);
    try {
      const res = await rfqApi.chat(rfqId, q);
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, response: res } : x)));
      const first = res.data.answer?.finding.citations[0];
      if (first) setTarget(first);
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error: describeError(e) } : x)));
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <div className="grid gap-3 p-3 md:p-4 lg:h-full lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)]">
      <section
        aria-labelledby="chat-title"
        className="flex min-h-[70dvh] flex-col rounded-panel border border-line bg-surface lg:min-h-0"
      >
        <header className="flex min-h-11 items-center gap-2 border-b border-line px-4 py-2">
          <h1 id="chat-title" className="text-dense font-semibold text-ink">
            RFQ에 묻기
          </h1>
          <ProvenanceBadge provenance={headerProvenance} />
          {!seesWholeSource(viewer) && (
            <span className="ml-auto inline-flex items-center gap-1 text-label text-ink-muted">
              <IconLock size={12} /> {scopeName(viewer)} 안에서만 답합니다
            </span>
          )}
        </header>
        <ol className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4" aria-live="polite" data-testid="chat-log">
          {turns.length === 0 && (
            <li className="text-dense text-ink-muted">
              답은 원문 문장을 인용해서만 하고, 근거가 없으면 답하지 않습니다.
            </li>
          )}
          {turns.map((t, i) => (
            <li key={i} className="flex flex-col gap-2">
              <p className="max-w-[85%] self-end rounded-panel bg-ink px-3 py-2 text-dense text-white">{t.question}</p>
              {!t.response && !t.error && <p className="pulse-soft text-label text-ink-muted">답을 찾는 중…</p>}
              {t.error && <ErrorState message={t.error} />}
              {t.response && <Answer response={t.response} target={target} onCite={setTarget} />}
            </li>
          ))}
        </ol>
        <div className="flex flex-wrap gap-1.5 border-t border-line px-4 pt-3">
          {lastSuggestions.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => void ask(s)}
              disabled={isBusy}
              className="rounded-full border border-line px-2.5 py-1 text-label text-ink-muted transition-soft hover:border-line-control hover:text-ink disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void ask(draft);
          }}
          className="flex gap-2 p-4"
        >
          <label className="sr-only" htmlFor="chat-input">
            질문
          </label>
          <input
            id="chat-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={500}
            placeholder="예: 인도조건은 무엇인가요?"
            className="min-h-10 min-w-0 flex-1 rounded-control border border-line-control bg-surface px-3 text-body text-ink"
            data-testid="chat-input"
          />
          <Button type="submit" variant="primary" disabled={!draft.trim() || isBusy} aria-label="질문 보내기">
            <IconArrowRight size={16} />
          </Button>
        </form>
      </section>
      <SourceViewer rfqId={rfqId} target={target} className="min-h-[70dvh] lg:min-h-0" />
    </div>
  );
}

type AnswerProps = { response: ChatResponse; target: Locus | null; onCite: (l: Locus) => void };

function Answer({ response, target, onCite }: AnswerProps) {
  const { status, answer, message, mode } = response.data;
  if (status !== "answered" || !answer) {
    return (
      <div
        data-testid={`chat-${status}`}
        className="flex max-w-[85%] items-start gap-2 rounded-panel border border-line bg-canvas px-3 py-2 text-dense text-ink-muted"
      >
        {status === "restricted" && <IconLock size={14} className="mt-1 shrink-0" />}
        {message}
      </div>
    );
  }
  const verified = answer.citation_status === "verified";
  return (
    <div
      className="flex max-w-[85%] flex-col gap-2 rounded-panel border border-line bg-surface px-3 py-2"
      data-testid="chat-answered"
      data-mode={mode}
    >
      <p className={cx("whitespace-pre-line text-ink", mode === "extractive" ? "font-serif text-source" : "text-body")}>
        {answer.finding.text}
      </p>
      <p className="text-label text-ink-muted" data-testid="chat-mode">
        {MODE_NOTE[mode]}
        {message && mode !== "scripted" ? ` · ${message}` : ""}
      </p>
      <div className="flex flex-wrap items-center gap-1">
        <ProvenanceBadge provenance={response.provenance} />
        {answer.finding.citations.map((l, i) => (
          <CitationChip key={locusKey(l, i)} locus={l} verified={verified} onOpen={onCite} isActive={sameLocus(target, l)} />
        ))}
      </div>
    </div>
  );
}
