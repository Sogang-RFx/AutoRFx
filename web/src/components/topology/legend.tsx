"use client";
/** What the colours of the boxes mean. Each swatch comes with a word and a count: colour is never the only cue. */
import type { ReactNode } from "react";
import { cx } from "@/components/ui/primitives";
import type { TopoModel } from "@/lib/topology/model";

function Swatch({ className, children }: { className: string; children: ReactNode }) {
  return (
    <li className="inline-flex items-center gap-1.5 text-label text-ink-muted tabular">
      <span aria-hidden="true" className={cx("inline-block size-2.5 rounded-sm", className)} />
      {children}
    </li>
  );
}

export function Legend({ model }: { model: TopoModel }) {
  const visible = model.clauses.length - model.hiddenClauses;
  return (
    <ul aria-label="범례" className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2">
      <Swatch className="bg-ink">조항 {visible}개</Swatch>
      <Swatch className="bg-danger">독소조항 {model.redFlags}개</Swatch>
      <Swatch className="bg-accent">고른 조항과 부서</Swatch>
      {model.hiddenClauses > 0 && <Swatch className="bg-line">열람 권한 없음 {model.hiddenClauses}개 (선 없음)</Swatch>}
    </ul>
  );
}
