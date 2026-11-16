"use client";
/**
 * Clause workspace (SPEC §14): ≥1280px three panes (list 380 · source flex ·
 * detail 340); 1024–1279px list 320 + source, detail opens over the source;
 * below 1024px list, detail and source stack.
 */
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { useTeamGroup } from "@/components/shell/app-shell";
import { SourceViewer } from "@/components/source-viewer/source-viewer";
import { cx } from "@/components/ui/primitives";
import { ClauseDetail, ClauseList } from "@/components/workspace/clause-panels";
import type { Locus } from "@/lib/rfq/contract";
import { rfqApi } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";
import { useResource } from "@/lib/rfq/use-resource";

export default function ClausesPage() {
  const { id } = useParams<{ id: string }>();
  const session = useSession();
  // A team change remounts the workspace: selection, filter and the loaded
  // PDF all reset, so nothing from the previous team stays on screen.
  return <Workspace key={session.team ?? "none"} rfqId={decodeURIComponent(id)} />;
}

function Workspace({ rfqId }: { rfqId: string }) {
  const session = useSession();
  const { restricted, ready: teamReady } = useTeamGroup();
  const ctx = useMemo(() => ({ team: session.team, scenario: session.scenario }), [session.team, session.scenario]);

  const [department, setDepartment] = useState<string | null>(session.team);
  const [selected, setSelected] = useState<string | null>(null);
  const [target, setTarget] = useState<Locus | null>(null);
  const [isDetailOpen, setIsDetailOpen] = useState(false);

  const meta = useResource(session.ready ? "meta" : null, (s) => rfqApi.meta(s));
  const departments = meta.status === "ok" ? meta.data.data.departments : [];

  const key = session.ready ? `clauses:${rfqId}:${ctx.team ?? "none"}:${department ?? "all"}:${ctx.scenario}` : null;
  const clauses = useResource(key, (s) => rfqApi.clauses(rfqId, ctx, department, s));
  const items = clauses.status === "ok" ? clauses.data.data.clauses : [];
  const provenance = clauses.status === "ok" ? clauses.data.provenance : undefined;
  const current = items.find((c) => c.section_id === selected) ?? null;

  function select(sectionId: string) {
    setSelected(sectionId);
    setIsDetailOpen(true);
    const c = items.find((i) => i.section_id === sectionId);
    if (c?.kind === "clause") setTarget(c.summary.finding.citations[0] ?? c.excerpt[0]?.locus ?? null);
    else setTarget(null);
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
          setSelected(null);
          setTarget(null);
        }}
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
        <SourceViewer rfqId={rfqId} ctx={ctx} restricted={teamReady && restricted} target={target} className="h-full" />
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
