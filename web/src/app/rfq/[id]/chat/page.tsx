"use client";
/**
 * RFQ chatbot (TASKS S016, SPEC §14, US6). The mock answers prepared
 * questions only and says so for anything else; it never invents an answer.
 * Citations open the source viewer. The conversation resets on team change.
 */
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { useTeamGroup } from "@/components/shell/app-shell";
import { SourceViewer } from "@/components/source-viewer/source-viewer";
import { IconArrowRight, IconLock } from "@/components/ui/icons";
import { Button, CitationChip, ErrorState, ProvenanceBadge, cx } from "@/components/ui/primitives";
import { modeOf } from "@/lib/rfq/config";
import type { ChatResponse, Locus } from "@/lib/rfq/contract";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";

type Turn = { question: string; response: ChatResponse | null; error: string | null };

const STARTERS = ["지체상금에 상한이 있나요?", "PPAP는 어느 레벨로 언제 제출하나요?", "견적 마감일은 언제인가요?"];

export default function ChatPage() {
  const { id } = useParams<{ id: string }>();
  const session = useSession();
  return <Chat key={session.team ?? "none"} rfqId={decodeURIComponent(id)} />;
}

function Chat({ rfqId }: { rfqId: string }) {
  const session = useSession();
  const { restricted: isRestricted, ready: teamReady } = useTeamGroup();
  const restricted = teamReady && isRestricted;
  const ctx = useMemo(() => ({ team: session.team }), [session.team]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [target, setTarget] = useState<Locus | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  const lastResponse = [...turns].reverse().find((t) => t.response)?.response ?? null;
  const lastSuggestions = lastResponse?.data.suggestions ?? STARTERS;
  // Before the first answer the badge follows the configured mode; after that,
  // the server's provenance (INV-10: never decided by the screen on its own).
  const headerProvenance = lastResponse?.provenance ?? (modeOf("chat") === "mock" ? { chat: "mock" as const } : undefined);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || isBusy) return;
    setDraft("");
    setIsBusy(true);
    setTurns((t) => [...t, { question: q, response: null, error: null }]);
    try {
      const res = await rfqApi.chat(rfqId, q, ctx);
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
          {restricted && (
            <span className="ml-auto inline-flex items-center gap-1 text-label text-ink-muted">
              <IconLock size={12} /> 금액 조항 제외
            </span>
          )}
        </header>
        <ol className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4" aria-live="polite" data-testid="chat-log">
          {turns.length === 0 && (
            <li className="text-dense text-ink-muted">답은 원문 문장을 인용해서만 합니다. 목업 챗봇은 준비된 질문에만 답합니다.</li>
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
            placeholder="예: 지체상금에 상한이 있나요?"
            className="min-h-10 min-w-0 flex-1 rounded-control border border-line-control bg-surface px-3 text-body text-ink"
            data-testid="chat-input"
          />
          <Button type="submit" variant="primary" disabled={!draft.trim() || isBusy} aria-label="질문 보내기">
            <IconArrowRight size={16} />
          </Button>
        </form>
      </section>
      <SourceViewer
        key={ctx.team ?? "none"}
        rfqId={rfqId}
        ctx={ctx}
        restricted={restricted}
        target={target}
        className="min-h-[70dvh] lg:min-h-0"
      />
    </div>
  );
}

type AnswerProps = { response: ChatResponse; target: Locus | null; onCite: (l: Locus) => void };

function Answer({ response, target, onCite }: AnswerProps) {
  const { status, answer, message } = response.data;
  if (status !== "answered" || !answer) {
    return (
      <div
        data-testid={`chat-${status}`}
        className={cx(
          "flex max-w-[85%] items-start gap-2 rounded-panel border px-3 py-2 text-dense",
          status === "restricted" ? "border-line bg-canvas text-ink-muted" : "mock-outline bg-mock-tint text-mock",
        )}
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
    >
      <p className="text-body text-ink">{answer.finding.text}</p>
      <div className="flex flex-wrap items-center gap-1">
        <ProvenanceBadge provenance={response.provenance} />
        {answer.finding.citations.map((l) => (
          <CitationChip
            key={`${l.page_number}-${l.sentence_index}`}
            locus={l}
            verified={verified}
            onOpen={onCite}
            isActive={target?.page_number === l.page_number && target.sentence_index === l.sentence_index}
          />
        ))}
      </div>
    </div>
  );
}
