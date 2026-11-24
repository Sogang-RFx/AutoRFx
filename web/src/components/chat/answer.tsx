"use client";
/**
 * One chat answer (DESIGN §7.5): every sentence with its own citations (INV-3), how it was
 * made (INV-12), and the conflicts between documents that touch it.
 */
import { IconLock, IconWarn } from "@/components/ui/icons";
import { CitationChip, ProvenanceBadge, cx, locusKey, sameLocus } from "@/components/ui/primitives";
import { shownStatements } from "@/lib/rfq/chat-history";
import type { ChatResponse, Locus } from "@/lib/rfq/contract";

type ChatData = ChatResponse["data"];

const MODE_NOTE: Record<ChatData["mode"], string> = {
  extractive: "원문 문장 그대로",
  generative: "LLM이 원문을 근거로 쓴 답",
  stored: "분석 단계에서 저장된 결과",
};

type CiteProps = { target: Locus | null; onCite: (l: Locus) => void };
type CitationsProps = CiteProps & { loci: Locus[]; verified: boolean };

function Citations({ loci, verified, target, onCite }: CitationsProps) {
  if (loci.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {loci.map((l, i) => (
        <CitationChip key={locusKey(l, i)} locus={l} verified={verified} onOpen={onCite} isActive={sameLocus(target, l)} />
      ))}
    </div>
  );
}

type ConflictsProps = CiteProps & { conflicts: ChatData["conflicts"] };

/**
 * The backend sends only conflict positions found in the RFQ's clauses, which is what makes a
 * consistency issue's citations verified.
 */
function Conflicts({ conflicts, target, onCite }: ConflictsProps) {
  if (conflicts.length === 0) return null;
  return (
    <div
      className="flex max-w-[85%] flex-col gap-2 rounded-panel border border-warn bg-warn-tint px-3 py-2"
      data-testid="chat-conflicts"
    >
      <p className="flex items-center gap-1.5 text-dense font-semibold text-warn">
        <IconWarn size={14} className="shrink-0" />
        문서마다 값이 다릅니다
      </p>
      <ul className="flex flex-col gap-2">
        {conflicts.map((c) => (
          <li key={c.id} className="flex flex-col gap-1">
            <p className="text-dense font-medium text-ink">{c.title}</p>
            {c.detail && <p className="text-dense whitespace-pre-line text-ink">{c.detail}</p>}
            <Citations loci={c.citations} verified target={target} onCite={onCite} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A reply without an answer: nothing found, refused, or how to use the chat. */
function Notice({ status, message }: Pick<ChatData, "status" | "message">) {
  return (
    <div
      data-testid={`chat-${status}`}
      className={cx(
        "flex max-w-[85%] items-start gap-2 rounded-panel border border-line bg-canvas px-3 py-2 text-dense",
        status === "guide" ? "text-ink" : "text-ink-muted",
      )}
    >
      {status === "restricted" && <IconLock size={14} className="mt-1 shrink-0" />}
      <p className="whitespace-pre-line">{message}</p>
    </div>
  );
}

type AnswerProps = CiteProps & { response: ChatResponse };

export function Answer({ response, target, onCite }: AnswerProps) {
  const { status, answer, message, mode, conflicts } = response.data;
  const statements = shownStatements(response.data);
  if (!answer || statements.length === 0) {
    return (
      <>
        <Notice status={status} message={message} />
        <Conflicts conflicts={conflicts} target={target} onCite={onCite} />
      </>
    );
  }
  const verified = answer.citation_status === "verified";
  return (
    <>
      <div
        className="flex max-w-[85%] flex-col gap-2 rounded-panel border border-line bg-surface px-3 py-2"
        data-testid="chat-answered"
        data-mode={mode}
      >
        <ul className="flex flex-col gap-3">
          {statements.map((s, i) => (
            <li key={i} className="flex flex-col gap-1" data-testid="chat-statement">
              <p className={cx("whitespace-pre-line text-ink", mode === "extractive" ? "font-serif text-source" : "text-body")}>
                {s.text}
              </p>
              <Citations loci={s.citations} verified={verified} target={target} onCite={onCite} />
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap items-center gap-2">
          <ProvenanceBadge provenance={response.provenance} />
          <p className="text-label text-ink-muted" data-testid="chat-mode">
            {MODE_NOTE[mode]}
            {message && ` · ${message}`}
          </p>
        </div>
      </div>
      <Conflicts conflicts={conflicts} target={target} onCite={onCite} />
    </>
  );
}
