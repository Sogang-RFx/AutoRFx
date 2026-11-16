"use client";
/**
 * Pre-cost simulator (TASKS S015, SPEC §8, US5): RFQ-derived input candidates
 * with citations → user-confirmed inputs → deterministic result. The Go/No-Go
 * slot stays "기준선 미정" until the domain team defines it (OQ-3).
 */
import { useParams } from "next/navigation";
import { useState } from "react";
import { useTeamGroup } from "@/components/shell/app-shell";
import { IconCalc, IconLock } from "@/components/ui/icons";
import {
  Button,
  CitationChip,
  EmptyState,
  ErrorState,
  Panel,
  ProvenanceBadge,
  Skeleton,
} from "@/components/ui/primitives";
import type { CostInputsResponse, PrecostResponse } from "@/lib/rfq/contract";
import { describeError, rfqApi } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";
import { useResource } from "@/lib/rfq/use-resource";

export default function PrecostPage() {
  const { id } = useParams<{ id: string }>();
  const rfqId = decodeURIComponent(id);
  const session = useSession();
  const { restricted, ready, error: metaError } = useTeamGroup();
  const inputs = useResource(ready && !restricted ? `cost:${rfqId}:${session.team}` : null, (s) =>
    rfqApi.costInputs(rfqId, { team: session.team }, s),
  );

  if (ready && restricted) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <Panel title="사전원가" labelId="precost-title">
          <EmptyState title="이 팀에는 원가 정보가 제공되지 않습니다">
            <p className="flex items-center gap-1.5">
              <IconLock size={14} /> 금액 열람 그룹(시연용 가정: 영업·원가·PM)으로 팀을 바꾸면 볼 수 있습니다.
            </p>
          </EmptyState>
        </Panel>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 p-3 md:p-6">
      <div>
        <h1 className="text-title font-semibold text-ink">사전원가</h1>
        <p className="text-body text-ink-muted">
          RFQ에서 찾은 입력 후보를 원문과 함께 확인하고, 확인한 값으로만 단위 원가를 계산합니다. 계산은 정해진 식으로 하며
          AI가 수치를 만들지 않습니다.
        </p>
      </div>
      {metaError && <ErrorState message={`팀 정보를 불러오지 못했습니다. ${metaError}`} />}
      {!metaError && (!ready || inputs.status === "loading") && <Skeleton lines={6} />}
      {inputs.status === "error" && <ErrorState message={inputs.error} />}
      {inputs.status === "ok" && <Simulator data={inputs.data} team={session.team} />}
    </div>
  );
}

type Form = { min_quantity: string; fixed_costs: string; variable_cost_per_unit: string; currency: string };

function initialForm(data: CostInputsResponse): Form {
  const get = (f: string) => data.data.candidates.find((c) => c.field === f)?.value ?? "";
  return {
    min_quantity: get("min_quantity"),
    fixed_costs: get("fixed_costs"),
    variable_cost_per_unit: get("variable_cost_per_unit"),
    currency: get("currency") || "USD",
  };
}

const DECIMAL = /^\d+(\.\d+)?$/;

function Simulator({ data, team }: { data: CostInputsResponse; team: string | null }) {
  const [form, setForm] = useState<Form>(() => initialForm(data));
  const [runs, setRuns] = useState<PrecostResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  const invalid =
    !/^\d+$/.test(form.min_quantity) ||
    Number(form.min_quantity) < 1 ||
    !DECIMAL.test(form.fixed_costs) ||
    !DECIMAL.test(form.variable_cost_per_unit) ||
    !/^[A-Z]{3}$/.test(form.currency);

  async function submit(e: { preventDefault: () => void }) {
    e.preventDefault();
    if (invalid) return;
    setIsBusy(true);
    setError(null);
    try {
      const res = await rfqApi.precost(
        {
          item: data.data.item,
          min_quantity: Number(form.min_quantity),
          fixed_costs: form.fixed_costs,
          variable_cost_per_unit: form.variable_cost_per_unit,
          currency: form.currency,
        },
        { team },
      );
      setRuns((r) => [res, ...r].slice(0, 5));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setIsBusy(false);
    }
  }

  const latest = runs[0];
  const field = (key: keyof Form, label: string, hint?: string) => (
    <label className="flex flex-col gap-1">
      <span className="text-label font-semibold text-ink-muted">{label}</span>
      <input
        value={form[key]}
        inputMode={key === "currency" ? "text" : "decimal"}
        onChange={(e) => setForm({ ...form, [key]: e.target.value.trim() })}
        className="min-h-10 rounded-control border border-line-control bg-surface px-3 text-body text-ink tabular"
        data-testid={`input-${key}`}
      />
      {hint && <span className="text-label text-ink-muted">{hint}</span>}
    </label>
  );

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <Panel title="RFQ에서 찾은 입력 후보" provenance={data.provenance} labelId="cand-title">
        <ul className="divide-y divide-line">
          {data.data.candidates.map((c) => (
            <li key={c.field} className="flex flex-col gap-1.5 px-4 py-3">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-dense font-semibold text-ink">{c.label}</span>
                <span className="ml-auto text-body font-semibold text-ink tabular">
                  {c.value ?? <span className="text-label font-medium text-warn">RFQ에 없음</span>}
                </span>
              </div>
              <p className="text-label text-ink-muted">{c.note}</p>
              {c.evidence && (
                <div className="flex flex-wrap gap-1">
                  {c.evidence.finding.citations.map((l) => (
                    <CitationChip
                      key={`${l.page_number}-${l.sentence_index}`}
                      locus={l}
                      verified={c.evidence?.citation_status === "verified"}
                    />
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      </Panel>

      <div className="flex flex-col gap-4">
        <Panel title="계산" labelId="calc-title">
          <form onSubmit={submit} className="grid gap-3 p-4 md:grid-cols-2">
            {field("min_quantity", "최소 수량 (개)")}
            {field("fixed_costs", "고정비 (금형비 + 개발비)")}
            {field("variable_cost_per_unit", "단위당 변동비", "RFQ에 없는 값 — 직접 입력")}
            {field("currency", "통화")}
            <p className="text-label text-ink-muted md:col-span-2">
              단위 원가 = 고정비 ÷ 최소 수량 + 단위당 변동비 · 소수 넷째 자리 반올림
            </p>
            <div className="flex flex-wrap items-center gap-2 md:col-span-2">
              <Button type="submit" variant="primary" disabled={invalid || isBusy} data-testid="calc">
                <IconCalc size={14} /> 계산
              </Button>
              {invalid && <span className="text-label text-ink-muted">모든 값을 숫자로 넣어 주세요.</span>}
            </div>
          </form>
          {error && <ErrorState message={error} />}
        </Panel>

        <Panel title="결과" labelId="result-title" provenance={latest?.provenance}>
          {!latest && <EmptyState title="아직 계산하지 않았습니다" />}
          {latest && (
            <div className="flex flex-col gap-4 p-4" data-testid="precost-result">
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-0.5 rounded-control bg-canvas p-3">
                  <span className="text-label text-ink-muted">단위당 고정비</span>
                  <span className="text-heading font-semibold text-ink tabular">
                    {latest.data.fixed_cost_per_unit} <span className="text-label">{latest.data.inputs.currency}</span>
                  </span>
                </div>
                <div className="flex flex-col gap-0.5 rounded-control bg-accent-tint p-3">
                  <span className="text-label text-accent">단위 원가</span>
                  <span className="text-title font-semibold text-ink tabular" data-testid="unit-cost">
                    {latest.data.unit_cost} <span className="text-label">{latest.data.inputs.currency}</span>
                  </span>
                </div>
              </div>
              <div className="flex flex-col gap-1 rounded-control border border-dashed border-line-control p-3">
                <span className="text-label font-semibold text-ink-muted">입찰 판단 (Go / No-Go)</span>
                <span className="text-body font-semibold text-ink">기준선 미정</span>
                <span className="text-label text-ink-muted">{latest.data.decision.note}</span>
              </div>
              <p className="flex flex-wrap items-center gap-2 text-label text-ink-muted">
                입력 후보 출처 <ProvenanceBadge provenance={data.provenance} /> · 계산식은 SPEC §8 그대로입니다.
              </p>
            </div>
          )}
        </Panel>

        {runs.length > 1 && (
          <Panel title="값 비교" labelId="cmp-title">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-dense tabular">
                <thead className="border-b border-line text-label text-ink-muted">
                  <tr>
                    <th scope="col" className="px-4 py-2">최소 수량</th>
                    <th scope="col" className="px-3 py-2">고정비</th>
                    <th scope="col" className="px-3 py-2">변동비</th>
                    <th scope="col" className="px-3 py-2">단위 원가</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r, i) => (
                    <tr key={runs.length - i} className="border-b border-line last:border-0">
                      <td className="px-4 py-2">{r.data.inputs.min_quantity.toLocaleString("ko-KR")}</td>
                      <td className="px-3 py-2">{r.data.inputs.fixed_costs}</td>
                      <td className="px-3 py-2">{r.data.inputs.variable_cost_per_unit}</td>
                      <td className="px-3 py-2 font-semibold text-ink">
                        {r.data.unit_cost} {r.data.inputs.currency}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}
