"use client";
/**
 * RFQ chatbot (TASKS S016, docs/design/chat.md, US6). Answers come from the backend and
 * quote the source; with no evidence it does not answer. Citations open the
 * source viewer. The conversation resets when the account changes, so the
 * history sent with a question never crosses accounts (INV-8).
 */
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Composer } from "@/components/chat/composer";
import { Conversation } from "@/components/chat/conversation";
import { SourceViewer } from "@/components/source-viewer/source-viewer";
import { IconLock } from "@/components/ui/icons";
import { ProvenanceBadge } from "@/components/ui/primitives";
import { buildHistory, firstCitation, type Turn } from "@/lib/rfq/chat-history";
import type { Locus } from "@/lib/rfq/contract";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { scopeName, seesWholeSource } from "@/lib/rfq/scope";
import { useViewer } from "@/lib/rfq/session";

/** First suggestions before any answer: general RFQ questions. */
const STARTERS = ["RFQ 요약해줘", "우리한테 불리한 조항은?", "문서끼리 값이 다른 곳은?", "납기 조건은?"];

export default function ChatPage() {
  const { id } = useParams<{ id: string }>();
  const viewer = useViewer();
  return <Chat key={`${id}:${viewer.user_id}`} rfqId={decodeURIComponent(id)} />;
}

function Chat({ rfqId }: { rfqId: string }) {
  const viewer = useViewer();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [target, setTarget] = useState<Locus | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);

  const lastResponse = [...turns].reverse().find((t) => t.response)?.response ?? null;
  const suggested = lastResponse?.data.suggestions ?? [];
  const suggestions = suggested.length > 0 ? suggested : STARTERS;

  function finishTurn(result: Partial<Turn>) {
    setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? { ...t, ...result } : t)));
  }

  async function ask(question: string) {
    if (isBusy) return;
    const controller = new AbortController();
    pending.current = controller;
    const history = buildHistory(turns);
    setIsBusy(true);
    setTurns((ts) => [...ts, { question, response: null, error: null, startedAt: Date.now() }]);
    try {
      const response = await rfqApi.chat(rfqId, question, history, controller.signal);
      if (controller.signal.aborted) return;
      finishTurn({ response });
      const first = firstCitation(response.data);
      if (first) setTarget(first);
    } catch (e) {
      if (controller.signal.aborted) return;
      finishTurn({ error: describeError(e) });
    } finally {
      if (!controller.signal.aborted) setIsBusy(false);
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
          {/* The badge follows the server's provenance (INV-10: never decided by the screen). */}
          <ProvenanceBadge provenance={lastResponse?.provenance} />
          {!seesWholeSource(viewer) && (
            <span className="ml-auto inline-flex items-center gap-1 text-label text-ink-muted">
              <IconLock size={12} /> {scopeName(viewer)} 안에서만 답합니다
            </span>
          )}
        </header>
        <Conversation turns={turns} target={target} onCite={setTarget} />
        <Composer suggestions={suggestions} isBusy={isBusy} onAsk={(q) => void ask(q)} />
      </section>
      <SourceViewer rfqId={rfqId} target={target} className="min-h-[70dvh] lg:min-h-0" />
    </div>
  );
}
