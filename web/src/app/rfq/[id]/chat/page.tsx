"use client";
/**
 * RFQ chatbot (TASKS S016, docs/design/chat.md, docs/design/immersive-ui.md §4.4 and §6). Answers come from the
 * backend and quote the source; with no evidence it does not answer. While an answer is made the screen shows the
 * stage the stream reports, never a draft. Citations open the source viewer. The conversation resets when the
 * account changes, so the history sent with a question never crosses accounts (INV-8).
 */
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { EmptyState } from "@/components/chat/empty-state";
import { Composer } from "@/components/chat/composer";
import { Conversation } from "@/components/chat/conversation";
import { useChat } from "@/components/chat/use-chat";
import { useReveal } from "@/components/fx/reveal";
import { SourceViewer } from "@/components/source-viewer/source-viewer";
import { IconLock } from "@/components/ui/icons";
import { ProvenanceBadge } from "@/components/ui/primitives";
import { useVoice } from "@/components/voice/use-voice";
import { MAX_QUESTION_CHARS, isTurnWaiting } from "@/lib/rfq/chat-history";
import { scopeName, seesWholeSource } from "@/lib/rfq/scope";
import { useViewer } from "@/lib/rfq/session";
import { useMeta } from "@/lib/rfq/use-meta";
import { checkQuestion } from "@/components/chat/question";

/** First suggestions before any answer: general RFQ questions. */
const STARTERS = ["RFQ 요약해줘", "우리한테 불리한 조항은?", "문서끼리 값이 다른 곳은?", "납기 조건은?"];
const TITLE_REVEAL = { duration: 520, maxDots: 1500 } as const;

export default function ChatPage() {
  const { id } = useParams<{ id: string }>();
  const viewer = useViewer();
  return <Chat key={`${id}:${viewer.user_id}`} rfqId={decodeURIComponent(id)} />;
}

function Chat({ rfqId }: { rfqId: string }) {
  const viewer = useViewer();
  const { data: meta } = useMeta();
  const chat = useChat(rfqId);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const headerRef = useRef<HTMLElement>(null);
  useReveal(headerRef, TITLE_REVEAL);

  // The voice hook is built first and needs the handler below, which needs the voice: a ref joins the two.
  const onTranscript = useRef<(text: string) => void>(() => undefined);
  const voice = useVoice({ config: meta?.voice ?? null, onTranscript: (text) => onTranscript.current(text) });

  /**
   * Sends the question, or keeps it for the input with a line that says why (too long, or an answer is still being
   * made). A transcript goes through here too, so a spoken question is never lost. Returns whether it was sent.
   */
  function ask(text: string): boolean {
    const check = checkQuestion(text, chat.isBusy);
    if (!check.ok) {
      if (check.notice) {
        setDraft(text.trim());
        setNotice(check.notice);
      }
      return false;
    }
    voice.stopSpeaking();
    setNotice(null);
    void chat.ask(check.question).then((response) => response && voice.speakAnswer(response));
    return true;
  }

  useEffect(() => {
    onTranscript.current = (text) => void ask(text);
  });

  function changeDraft(text: string) {
    setDraft(text);
    // The "too long" line goes once the text fits.
    setNotice((current) => (current && text.length <= MAX_QUESTION_CHARS ? null : current));
  }

  const { turns } = chat;
  const lastResponse = [...turns].reverse().find((t) => t.response)?.response ?? null;
  const suggested = lastResponse?.data.suggestions ?? [];
  const suggestions = turns.length === 0 ? [] : suggested.length > 0 ? suggested : STARTERS;
  const newest = turns.at(-1);
  // One ripple of the sphere for each real event of the pending turn.
  const pulseKey = newest && isTurnWaiting(newest) ? newest.progress.events : 0;
  const orb = { phase: voice.state.phase, isBusy: chat.isBusy, level: voice.level, pulseKey };

  return (
    <div className="grid gap-3 p-3 md:p-4 lg:h-full lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)]">
      <section
        aria-labelledby="chat-title"
        className="flex min-h-[70dvh] flex-col rounded-panel border border-line bg-surface lg:min-h-0"
      >
        <header ref={headerRef} className="flex min-h-11 items-center gap-2 border-b border-line px-4 py-2">
          <h1 id="chat-title" data-particle-target="text" className="text-dense font-semibold text-ink">
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
        <Conversation
          turns={turns}
          target={chat.target}
          onCite={chat.setTarget}
          empty={<EmptyState orb={orb} starters={STARTERS} isBusy={chat.isBusy} onAsk={(q) => void ask(q)} />}
        />
        <Composer
          suggestions={suggestions}
          hasTurns={turns.length > 0}
          isBusy={chat.isBusy}
          draft={draft}
          onDraftChange={changeDraft}
          onAsk={ask}
          pulseKey={pulseKey}
          voice={voice}
          voiceConfig={meta?.voice ?? null}
          notice={notice}
        />
      </section>
      <SourceViewer rfqId={rfqId} target={chat.target} className="min-h-[70dvh] lg:min-h-0" />
    </div>
  );
}
