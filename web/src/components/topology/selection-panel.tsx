"use client";
/** The side panel: the chosen clause or department. Everything here comes from the responses; nothing is guessed. */
import Link from "next/link";
import { useState } from "react";
import { Button, DepartmentChips, EmptyState, Panel, PriorityChip, RedFlagChip } from "@/components/ui/primitives";
import { IconArrowRight } from "@/components/ui/icons";
import type { TopoClause, TopoModel } from "@/lib/topology/model";
import type { Resolved } from "@/lib/topology/selection";

const LIST_STEP = 100;

interface PanelProps {
  rfqId: string;
  model: TopoModel;
  chosen: Resolved;
  onPickClause: (id: string) => void;
  onClear: () => void;
}

function ClauseBody({ rfqId, model, clause }: { rfqId: string; model: TopoModel; clause: TopoClause }) {
  const dept = (i: number) => model.departments[i];
  const label = (id: string) => model.departments.find((d) => d.id === id)?.label ?? id;
  const file = model.files[clause.file];
  const href = `/rfq/${encodeURIComponent(rfqId)}/clauses?clause=${encodeURIComponent(clause.id)}`;
  return (
    <div className="flex flex-col gap-3 p-4" data-testid="topology-clause">
      <p className="text-label text-ink-muted">
        <span className="tabular">{file.tag}</span> {file.name}
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        {clause.priority && <PriorityChip priority={clause.priority} />}
        {clause.redFlag && <RedFlagChip label={clause.redFlag.label} />}
      </div>
      {clause.lead !== null && (
        <DepartmentChips primary={dept(clause.lead).id} refs={clause.refs.map((r) => dept(r).id)} label={label} maxRefs={6} />
      )}
      <Link
        href={href}
        className="inline-flex min-h-9 items-center gap-1 self-start text-dense font-medium text-accent hover:underline"
      >
        조항 화면에서 원문 보기
        <IconArrowRight size={14} />
      </Link>
    </div>
  );
}

function DepartmentBody({ model, index, onPickClause }: { model: TopoModel; index: number; onPickClause: (id: string) => void }) {
  const [shown, setShown] = useState(LIST_STEP);
  const dept = model.departments[index];
  const led = model.clauses.filter((c) => c.lead === index);
  return (
    <div className="flex flex-col gap-3 p-4" data-testid="topology-department">
      <p className="text-dense text-ink-muted tabular">
        주관 {dept.lead}개 · 참조 {dept.ref}개
      </p>
      {led.length === 0 ? (
        <p className="text-dense text-ink-muted">이 부서가 주관하는 조항은 없습니다.</p>
      ) : (
        <ul className="flex flex-col">
          {led.slice(0, shown).map((c) => (
            <li key={c.id} className="border-b border-line last:border-b-0">
              <button
                type="button"
                onClick={() => onPickClause(c.id)}
                className="flex min-h-9 w-full items-baseline gap-2 py-1 text-left text-dense text-ink hover:text-accent"
              >
                <span className="tabular text-ink-muted">{c.number}</span>
                <span className="min-w-0 flex-1 truncate">{c.title}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {led.length > shown && (
        <Button onClick={() => setShown((n) => n + LIST_STEP)} className="self-start">
          {led.length - shown}개 더 보기
        </Button>
      )}
    </div>
  );
}

export function SelectionPanel({ rfqId, model, chosen, onPickClause, onClear }: PanelProps) {
  const clause = chosen?.kind === "clause" ? model.clauses[chosen.index] : null;
  const title = clause
    ? `${clause.number} ${clause.title}`.trim()
    : chosen
      ? `${model.departments[chosen.index].label} 부서`
      : "선택";
  return (
    <Panel
      as="aside"
      title={title}
      labelId="topology-selection-title"
      className="lg:max-h-full"
      bodyClassName="overflow-y-auto"
      actions={
        chosen && (
          <Button onClick={onClear} className="min-h-8 px-2 text-label">
            선택 해제
          </Button>
        )
      }
    >
      {!chosen && (
        <EmptyState title="조항이나 부서를 고르세요">
          <p>상자를 누르면 번호와 제목, 주관·참조 부서를 볼 수 있습니다. 부서를 고르면 그 부서로 가는 선이 강조됩니다.</p>
        </EmptyState>
      )}
      {clause && <ClauseBody rfqId={rfqId} model={model} clause={clause} />}
      {chosen?.kind === "department" && (
        <DepartmentBody key={chosen.index} model={model} index={chosen.index} onPickClause={onPickClause} />
      )}
    </Panel>
  );
}
