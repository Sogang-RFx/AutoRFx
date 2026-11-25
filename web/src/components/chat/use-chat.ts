"use client";
/**
 * The conversation of the chat screen: turns, the open citation, and the one running request. The rules of the
 * turns are in `chatReducer` (lib/rfq/chat-history.ts); this hook sends the question, feeds the stream's progress
 * to the reducer and aborts the request when the screen goes away. The screen is keyed by account, so a new
 * account starts with an empty conversation and history never crosses accounts (INV-8).
 */
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { buildHistory, chatReducer, firstCitation, isWaiting, type Turn } from "@/lib/rfq/chat-history";
import type { ChatResponse, Locus } from "@/lib/rfq/contract";
import { describeError, rfqApi } from "@/lib/rfq/provider";

export interface Chat {
  turns: Turn[];
  isBusy: boolean;
  target: Locus | null;
  setTarget: (locus: Locus | null) => void;
  /** Resolves to the answer, or null when it failed, was aborted, or another question was still running. */
  ask: (question: string) => Promise<ChatResponse | null>;
}

export function useChat(rfqId: string): Chat {
  const [turns, dispatch] = useReducer(chatReducer, [] as Turn[]);
  const [target, setTarget] = useState<Locus | null>(null);
  const pending = useRef<AbortController | null>(null);
  const turnsRef = useRef<Turn[]>(turns);
  useEffect(() => {
    turnsRef.current = turns;
  });
  useEffect(() => () => pending.current?.abort(), []);

  const ask = useCallback(
    async (question: string): Promise<ChatResponse | null> => {
      // A request that is still running holds the next question back, even before the screen has caught up.
      if (pending.current && !pending.current.signal.aborted) return null;
      const controller = new AbortController();
      pending.current = controller;
      const history = buildHistory(turnsRef.current);
      dispatch({ type: "asked", question, at: Date.now() });
      try {
        const response = await rfqApi.chatStream(rfqId, question, history, {
          signal: controller.signal,
          onEvent: (event) => dispatch({ type: "event", event }),
          onRestart: () => dispatch({ type: "restarted" }),
        });
        if (controller.signal.aborted) return null;
        dispatch({ type: "answered", response });
        const first = firstCitation(response.data);
        if (first) setTarget(first);
        return response;
      } catch (e) {
        if (!controller.signal.aborted) dispatch({ type: "failed", message: describeError(e) });
        return null;
      } finally {
        if (pending.current === controller && !controller.signal.aborted) pending.current = null;
      }
    },
    [rfqId],
  );

  return { turns, isBusy: isWaiting(turns), target, setTarget, ask };
}
