"use client";
/** Header of the screens outside one RFQ (settings, accounts): brand, title, account. */
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { AccountBar } from "./account-bar";

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  const router = useRouter();
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <Link href="/" className="font-serif text-heading font-semibold text-ink">
        AutoRFx
      </Link>
      <h1 className="text-heading font-semibold text-ink">{title}</h1>
      {children}
      <div className="ml-auto flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => router.back()} className="text-label text-ink-muted hover:text-ink">
          ← 돌아가기
        </button>
        <AccountBar />
      </div>
    </header>
  );
}
