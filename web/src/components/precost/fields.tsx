/** Small pieces shared by the pre-cost panels. */
import type { ReactNode } from "react";

export const input = "min-h-9 w-full rounded-control border border-line-control bg-surface px-2 text-dense text-ink";

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-label font-semibold text-ink-muted">{label}</span>
      {children}
      {hint && <span className="text-label text-ink-muted">{hint}</span>}
    </label>
  );
}

export function Step({ n, title, children, aside }: { n: string; title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 border-t border-line px-4 py-4 first:border-t-0">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="text-dense font-semibold text-ink">
          <span className="mr-1.5 text-ink-muted">{n}</span>
          {title}
        </h3>
        {aside && <div className="ml-auto">{aside}</div>}
      </div>
      {children}
    </section>
  );
}
