/**
 * Frame of the screens before the app: the source sheet on the left, the form on the right. The
 * sheet shows what an account changes: the same document, some clauses open and some locked.
 */
import type { ReactNode } from "react";
import { IconLock } from "../ui/icons";
import { cx } from "../ui/primitives";

/** One row of the drawn sheet: widths of its text lines in percent, or a locked clause. */
const ROWS: Array<{ section: string; lines: number[] | null }> = [
  { section: "1", lines: [92, 64] },
  { section: "2", lines: null },
  { section: "3", lines: [88, 96, 40] },
  { section: "4", lines: null },
  { section: "5", lines: null },
  { section: "6", lines: [78, 90] },
];

function ScopeSheet() {
  return (
    <div aria-hidden="true" data-particle-target="box" className="relative max-w-112">
      <div className="absolute inset-x-3 top-2 -bottom-2 rounded-panel border border-line bg-canvas" />
      <ol className="relative flex flex-col rounded-panel border border-line bg-surface px-6 py-4">
        {ROWS.map((r) => (
          <li key={r.section} className="flex gap-4 border-b border-line py-3 last:border-b-0">
            <span className="w-6 shrink-0 font-serif text-source text-ink-muted tabular">{r.section}.</span>
            {r.lines ? (
              <span className="flex flex-1 flex-col justify-center gap-2">
                {r.lines.map((w, i) => (
                  <span
                    key={w}
                    className={cx("h-1.5 rounded-chip", i === 0 ? "bg-ink" : "bg-line-control")}
                    style={{ width: `${w}%` }}
                  />
                ))}
              </span>
            ) : (
              <span className="flex h-7 flex-1 items-center rounded-chip bg-canvas px-2 text-ink-muted">
                <IconLock size={14} />
              </span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

export function AuthFrame({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <aside className="hidden flex-col justify-center gap-8 border-r border-line bg-surface-source px-12 py-12 lg:flex xl:px-16">
        <p data-particle-target="text" className="font-serif text-title text-ink">
          같은 RFQ를,
          <br />
          <span className="italic">부서마다 맡은 만큼</span> 봅니다.
        </p>
        <ScopeSheet />
        <p data-particle-target="text" data-particle-tone="muted" className="max-w-112 text-body text-ink-muted">
          내 부서가 주관하거나 참조하는 조항이 열리고, 나머지는 번호와 자물쇠만 남습니다.
        </p>
      </aside>
      <main className="flex flex-col justify-center px-4 py-12 md:px-8">
        <div className="mx-auto flex w-full max-w-96 flex-col gap-6">
          <p data-particle-target="text" className="font-serif text-heading font-semibold text-ink">
            AutoRFx <span className="font-sans text-label font-medium text-ink-muted">자동차 부품 RFQ 분석</span>
          </p>
          {children}
        </div>
      </main>
    </div>
  );
}
