"use client";
/**
 * Clause workspace (SPEC §14): ≥1280px three panes (list 380 · source flex ·
 * detail 340); 1024–1279px list 320 + source, detail opens over the source;
 * below 1024px list, detail and source stack. `?clause=<clause_id>` opens a
 * clause directly (links from RTM and the consistency screen).
 */
import { useParams, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { SourceViewer } from "@/components/source-viewer/source-viewer";
import { cx } from "@/components/ui/primitives";
import { ClauseDetail, ClauseList, type DeptRole } from "@/components/workspace/clause-panels";
import type { ClauseItem, Locus } from "@/lib/rfq/contract";
import { rfqApi } from "@/lib/rfq/provider";
import { useViewer } from "@/lib/rfq/session";
import { useMeta } from "@/lib/rfq/use-meta";
import { useResource } from "@/lib/rfq/use-resource";

export default function ClausesPage() {
  const { id } = useParams<{ id: string }>();
  const viewer = useViewer();
  // Another account starts afresh: selection, filter and the loaded PDF all reset.
  return <Workspace key={viewer.user_id} rfqId={decodeURIComponent(id)} />;
}

function firstLocus(c: ClauseItem | undefined): Locus | null {
  if (c?.kind !== "clause") return null;
  return c.summary.finding.citations[0] ?? c.excerpt[0]?.locus ?? null;
}

function Workspace({ rfqId }: { rfqId: string }) {
  const search = useSearchParams();
  const { departments } = useMeta();

  // The account is who is looking; the department is only a view of what they may see.
  const [department, setDepartment] = useState<string | null>(null);
  const [role, setRole] = useState<DeptRole>("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [target, setTarget] = useState<Locus | null>(null);
  const [isDetailOpen, setIsDetailOpen] = useState(false);

  const clauses = useResource(`clauses:${rfqId}`, (s) => rfqApi.clauses(rfqId, s));
  const items = useMemo(() => (clauses.status === "ok" ? clauses.data.data.clauses : []), [clauses]);
  const provenance = clauses.status === "ok" ? clauses.data.provenance : undefined;
  const current = items.find((c) => c.clause_id === selected) ?? null;

  // Deep link: open the requested clause once, after the list arrives.
  const wanted = search.get("clause");
  const [linked, setLinked] = useState(false);
  if (!linked && wanted && clauses.status === "ok") {
    setLinked(true);
    const found = items.find((c) => c.clause_id === wanted);
    if (found) {
      setSelected(found.clause_id);
      setIsDetailOpen(true);
      setTarget(firstLocus(found));
    }
  }

  function select(clauseId: string) {
    setSelected(clauseId);
    setIsDetailOpen(true);
    setTarget(firstLocus(items.find((i) => i.clause_id === clauseId)));
  }

  return (
    <div className="grid gap-3 p-3 md:p-4 lg:h-full lg:grid-cols-[320px_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)] xl:grid-cols-[380px_minmax(0,1fr)_340px]">
      <ClauseList
        className="max-h-[70dvh] lg:max-h-none"
        status={clauses.status}
        error={clauses.error}
        clauses={items}
        provenance={provenance}
        departments={departments}
        department={department}
        onDepartment={(d) => {
          setDepartment(d);
          setRole("all");
          setSelected(null);
          setTarget(null);
        }}
        role={role}
        onRole={setRole}
        selected={selected}
        onSelect={select}
      />
      <ClauseDetail
        className="lg:hidden xl:flex"
        clause={current}
        provenance={provenance}
        departments={departments}
        target={target}
        onCite={setTarget}
      />
      <div className="relative min-h-[70dvh] min-w-0 lg:order-first lg:col-start-2 lg:row-start-1 lg:min-h-0">
        <SourceViewer rfqId={rfqId} target={target} className="h-full" />
        {/* 1024–1279px: detail floats over the source */}
        <div
          className={cx(
            "absolute inset-y-3 right-3 z-10 hidden w-[340px] max-w-[calc(100%-24px)] xl:hidden",
            isDetailOpen && current && "lg:flex",
          )}
        >
          <ClauseDetail
            className="w-full shadow-float"
            clause={current}
            provenance={provenance}
            departments={departments}
            target={target}
            onCite={setTarget}
            onClose={() => setIsDetailOpen(false)}
            idPrefix="float-detail"
          />
        </div>
      </div>
    </div>
  );
}
