"use client";
/** Opt a screen part in to the reveal: elements marked `data-particle-target` inside it fly in as dots. */
import { useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";
import { loadGate } from "@/lib/fx/first-load";
import type { RevealOptions } from "@/lib/fx/reveal";
import { useFx } from "./fx-provider";

export interface UseRevealOptions extends RevealOptions {
  /** Reveal again when this changes (a new title, for example). */
  revealKey?: string | number;
}

/**
 * Reveals the targets under `ref` when the component mounts (and when `revealKey` changes). It runs before paint, so
 * a hidden element never shows for a frame, and it undoes itself on unmount. With effects off, with reduced motion,
 * or outside a provider it does nothing and the content is just there. It also does nothing for what the server
 * already painted on a hard load (the text stays visible: no flash); only later mounts are revealed.
 */
export function useReveal(ref: RefObject<HTMLElement | null>, options: UseRevealOptions = {}): void {
  const fx = useFx();
  const { duration, maxDots, revealKey } = options;
  useLayoutEffect(() => {
    const root = ref.current;
    if (!fx || !root || !loadGate.shouldReveal()) return;
    const handle = fx.reveal(root, { duration, maxDots });
    return () => handle.cancel();
  }, [fx, ref, duration, maxDots, revealKey]);
}

export interface RevealProps extends UseRevealOptions {
  as?: "div" | "section" | "header" | "article";
  className?: string;
  children: ReactNode;
}

/** A wrapper element that reveals its targets on mount. */
export function Reveal({ as = "div", className, children, ...options }: RevealProps) {
  const ref = useRef<HTMLElement>(null);
  useReveal(ref, options);
  const Tag = as;
  return (
    <Tag ref={ref as RefObject<never>} className={className}>
      {children}
    </Tag>
  );
}
