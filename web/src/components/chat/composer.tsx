"use client";
/** Suggested questions and the question field of the chat. */
import { useState } from "react";
import { IconArrowRight } from "@/components/ui/icons";
import { Button } from "@/components/ui/primitives";
import { MAX_QUESTION_CHARS } from "@/lib/rfq/chat-history";

type ComposerProps = { suggestions: string[]; isBusy: boolean; onAsk: (question: string) => void };

export function Composer({ suggestions, isBusy, onAsk }: ComposerProps) {
  const [draft, setDraft] = useState("");

  function submit(text: string) {
    const question = text.trim();
    if (!question || isBusy) return;
    setDraft("");
    onAsk(question);
  }

  return (
    <>
      <div className="flex flex-wrap gap-1.5 border-t border-line px-4 pt-3">
        {suggestions.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => submit(s)}
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
          submit(draft);
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
          maxLength={MAX_QUESTION_CHARS}
          placeholder="예: 인도조건은 무엇인가요?"
          className="min-h-10 min-w-0 flex-1 rounded-control border border-line-control bg-surface px-3 text-body text-ink"
          data-testid="chat-input"
        />
        <Button type="submit" variant="primary" disabled={!draft.trim() || isBusy} aria-label="질문 보내기">
          <IconArrowRight size={16} />
        </Button>
      </form>
    </>
  );
}
