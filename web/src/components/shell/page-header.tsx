"use client";
/**
 * Header of the screens outside one RFQ (settings, accounts): brand, title, account.
 * The title and the optional lead line are particle targets: on mount they form out of dots (or out of the
 * previous screen's header), within 520 ms and 1,500 dots (docs/design/immersive-ui.md §4.2).
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, type ReactNode } from "react";
import { useReveal } from "../fx/reveal";
import { AccountBar } from "./account-bar";

const HEADER_REVEAL = { duration: 520, maxDots: 1500 } as const;

export function PageHeader({ title, lead, children }: { title: string; lead?: string; children?: ReactNode }) {
  const router = useRouter();
  const ref = useRef<HTMLElement>(null);
  useReveal(ref, { ...HEADER_REVEAL, revealKey: title });
  return (
    <header ref={ref} className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <Link href="/" className="font-serif text-heading font-semibold text-ink">
        AutoRFx
      </Link>
      <h1 data-particle-target="text" className="text-heading font-semibold text-ink">
        {title}
      </h1>
      {children}
      <div className="ml-auto flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => router.back()} className="text-label text-ink-muted hover:text-ink">
          ← 돌아가기
        </button>
        <AccountBar />
      </div>
      {lead && (
        <p data-particle-target="text" data-particle-tone="muted" className="w-full text-dense text-ink-muted">
          {lead}
        </p>
      )}
    </header>
  );
}
