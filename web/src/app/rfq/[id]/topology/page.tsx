"use client";
/**
 * RFQ flow map (docs/design/immersive-ui.md §4.5): files on the left, clauses in the middle, lead
 * departments on the right, one line from each clause to its lead department. Built only from the
 * clauses, the package files and the consistency response the other screens already use. The 3D scene is a
 * separate chunk behind a WebGL check; the same content is always available as tables.
 */
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { Reveal } from "@/components/fx/reveal";
import { Legend } from "@/components/topology/legend";
import { ListView } from "@/components/topology/list-view";
import { SceneLoader, useWebGl } from "@/components/topology/scene-loader";
import { SelectionPanel } from "@/components/topology/selection-panel";
import { IconList, IconTopology } from "@/components/ui/icons";
import { Button, EmptyState, ErrorState, ForbiddenState, Panel, Skeleton } from "@/components/ui/primitives";
import { usePackageFiles } from "@/lib/rfq/package-context";
import { rfqApi } from "@/lib/rfq/provider";
import { useViewer } from "@/lib/rfq/session";
import { useMeta } from "@/lib/rfq/use-meta";
import { useResource } from "@/lib/rfq/use-resource";
import { computeLayout } from "@/lib/topology/layout";
import { buildModel, type TopoModel } from "@/lib/topology/model";
import { flowOnReturn } from "@/lib/topology/flow";
import { resolveSelection, type Selection } from "@/lib/topology/selection";
import { mapSummary } from "@/lib/topology/tables";

const LOAD_ERROR_TEXT = "조항을 불러오지 못했습니다. 잠시 뒤에 다시 열어 주세요.";
const FORBIDDEN_TEXT = "이 계정으로는 이 RFQ의 조항을 볼 수 없습니다.";

export default function TopologyPage() {
  const { id } = useParams<{ id: string }>();
  const viewer = useViewer();
  // Another account starts afresh: selection and what the scene drew are dropped.
  return <Topology key={viewer.user_id} rfqId={decodeURIComponent(id)} />;
}

function Heading({ lead }: { lead: string | null }) {
  // One h1 for the whole life of the page; only the lead line flies in, once it first has content.
  return (
    <header className="flex flex-col gap-2">
      <h1 className="text-title font-semibold text-ink">흐름도</h1>
      {lead !== null && (
        <Reveal duration={520} maxDots={1500}>
          <p data-particle-target="text" data-particle-tone="muted" className="text-body text-ink-muted tabular">
            {lead}
          </p>
        </Reveal>
      )}
    </header>
  );
}

function Topology({ rfqId }: { rfqId: string }) {
  const files = usePackageFiles();
  const meta = useMeta();
  const clauses = useResource(`clauses:${rfqId}`, (s) => rfqApi.clauses(rfqId, s));
  const consistency = useResource(`consistency:${rfqId}`, (s) => rfqApi.consistency(rfqId, s));
  const webgl = useWebGl();
  const [selection, setSelection] = useState<Selection>(null);
  const [flow, setFlow] = useState({ token: 0, department: null as string | null });
  const [wantsList, setWantsList] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const settled = clauses.status !== "loading" && consistency.status !== "loading" && (meta.data !== null || meta.error !== null);
  const model = useMemo<TopoModel | null>(() => {
    if (clauses.status !== "ok" || !settled) return null;
    return buildModel({
      files,
      clauses: clauses.data.data.clauses,
      consistency: consistency.status === "ok" ? consistency.data.data : null,
      departments: meta.departments,
    });
  }, [clauses, consistency, files, meta.departments, settled]);
  const layout = useMemo(() => (model ? computeLayout(model) : null), [model]);
  const chosen = model ? resolveSelection(model, selection) : null;

  const sceneOk = webgl === true && failure === null;
  const showList = wantsList || webgl === false || failure !== null;

  function pickDepartment(id: string) {
    setSelection({ kind: "department", id });
    setFlow((f) => ({ token: f.token + 1, department: id }));
  }

  let body;
  if (clauses.status === "error") {
    body = clauses.forbidden ? <ForbiddenState message={FORBIDDEN_TEXT} /> : <ErrorState message={LOAD_ERROR_TEXT} />;
  } else if (!model || !layout) {
    body = <Skeleton lines={10} />;
  } else if (model.clauses.length === 0) {
    body = (
      <EmptyState title="표시할 조항이 없습니다">
        <p>이 RFQ에서 나눈 조항이 없거나, 이 계정이 볼 수 있는 조항이 없습니다.</p>
      </EmptyState>
    );
  } else if (showList) {
    body = (
      <>
        {webgl === false && <p className="px-4 pt-3 text-label text-ink-muted">이 브라우저에서는 3D 장면을 쓸 수 없어 목록으로 보입니다.</p>}
        {failure !== null && <p className="px-4 pt-3 text-label text-ink-muted">3D 장면을 그리지 못해 목록으로 보입니다.</p>}
        <ListView model={model} selection={selection} onPickDepartment={pickDepartment} />
      </>
    );
  } else if (webgl === null) {
    body = <Skeleton lines={10} />;
  } else {
    body = (
      <>
        <div className="h-[min(72dvh,680px)] min-h-96 bg-canvas" data-testid="topology-scene">
          <SceneLoader
            model={model}
            layout={layout}
            chosen={chosen}
            flow={flow}
            onPickClause={(i) => {
              const c = model.clauses[i];
              if (c?.isVisible) setSelection({ kind: "clause", id: c.id });
            }}
            onPickDepartment={(i) => pickDepartment(model.departments[i].id)}
            onFail={setFailure}
          />
        </div>
        <Legend model={model} />
      </>
    );
  }

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4 p-3 md:p-4">
      <Heading lead={model ? mapSummary(model) : null} />
      {consistency.status === "error" && !consistency.forbidden && (
        <p className="text-label text-ink-muted">정합성 정보를 불러오지 못해 이슈 수는 표시하지 않습니다.</p>
      )}
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel
          title={showList ? "목록" : "조항에서 주관 부서로"}
          labelId="topology-title"
          provenance={clauses.status === "ok" ? clauses.data.provenance : undefined}
          bodyClassName="overflow-hidden"
          actions={
            model &&
            model.clauses.length > 0 &&
            sceneOk && (
              <Button
                aria-pressed={wantsList}
                onClick={() => {
                  // Coming back from the list re-mounts the scene; its flow already played.
                  if (wantsList) setFlow(flowOnReturn);
                  setWantsList((v) => !v);
                }}
                className="min-h-8 px-2 text-label"
              >
                {wantsList ? <IconTopology size={14} /> : <IconList size={14} />}
                {wantsList ? "흐름도로 보기" : "목록으로 보기"}
              </Button>
            )
          }
        >
          {body}
        </Panel>
        {model && layout && model.clauses.length > 0 && (
          <SelectionPanel
            rfqId={rfqId}
            model={model}
            chosen={chosen}
            onPickClause={(id) => setSelection({ kind: "clause", id })}
            onClear={() => setSelection(null)}
          />
        )}
      </div>
    </div>
  );
}
