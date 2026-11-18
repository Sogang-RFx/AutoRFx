"use client";
/**
 * Pre-cost simulator (TASKS S015, SPEC §8, US5): RFQ-derived input candidates
 * with citations → user-confirmed inputs → deterministic result. When files
 * disagree on a cost driver, the user picks a value (`resolved`) or the
 * decision holds. The Go/No-Go status is what the server's ordered rules
 * computed; the screen states the rule and never adds a recommendation of its own.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import type { ReactNode } from "react";
import { useTeamGroup } from "@/components/shell/app-shell";
import { IconCalc, IconCheck, IconLock, IconPause, IconWarn } from "@/components/ui/icons";
import {
  Button,
  CitationChip,
  EmptyState,
  ErrorState,
  ForbiddenState,
  Panel,
  ProvenanceBadge,
  Skeleton,
  cx,
  locusKey,
} from "@/components/ui/primitives";
import type { CostInputCandidate, CostInputsResponse, DecisionStatus, PrecostResponse } from "@/lib/rfq/contract";
import { describeError, isForbidden, rfqApi } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";
import { useResource } from "@/lib/rfq/use-resource";

const RESTRICTED_NOTE = "이 팀은 금액 관련 정보를 볼 수 없습니다. 금액 열람 그룹(시연용 가정: 영업·구매·원가)으로 팀을 바꾸면 볼 수 있습니다.";

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
              <IconLock size={14} /> {RESTRICTED_NOTE}
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
          RFQ에서 찾은 입력 후보를 원문과 함께 확인하고, 확인한 값으로만 단위 원가를 계산합니다. 계산과 판정은 정해진 식과
          규칙으로 하며 AI가 수치를 만들지 않습니다.
        </p>
      </div>
      {metaError && <ErrorState message={`팀 정보를 불러오지 못했습니다. ${metaError}`} />}
      {!metaError && (!ready || inputs.status === "loading") && <Skeleton lines={6} />}
      {inputs.status === "error" &&
        (inputs.forbidden ? <ForbiddenState message={inputs.error} /> : <ErrorState message={inputs.error} />)}
      {inputs.status === "ok" && <Simulator data={inputs.data} team={session.team} rfqId={rfqId} />}
    </div>
  );
}

type Form = {
  min_quantity: string;
  fixed_costs: string;
  variable_cost_per_unit: string;
  currency: string;
  target_price: string;
};

function initialForm(data: CostInputsResponse): Form {
  const get = (f: string) => data.data.candidates.find((c) => c.field === f)?.value ?? "";
  return {
    min_quantity: get("min_quantity"),
    fixed_costs: get("fixed_costs"),
    variable_cost_per_unit: get("variable_cost_per_unit"),
    currency: get("currency") || "USD",
    target_price: get("target_price"),
  };
}

const DECIMAL = /^\d+(\.\d+)?$/;

const SOURCE_LABEL: Record<CostInputCandidate["source"], string> = {
  rfq: "RFQ에 적힘",
  assumption: "가정값",
  missing: "RFQ에 없음",
};

/** One picker per unresolved consistency issue: the candidate that lists the disagreeing values. */
function conflicts(candidates: CostInputCandidate[]): CostInputCandidate[] {
  const seen = new Set<string>();
  return candidates.filter((c) => {
    if (!c.issue_id || c.alternatives.length < 2 || seen.has(c.issue_id)) return false;
    seen.add(c.issue_id);
    return true;
  });
}

function Simulator({ data, team, rfqId }: { data: CostInputsResponse; team: string | null; rfqId: string }) {
  const [form, setForm] = useState<Form>(() => initialForm(data));
  const [resolved, setResolved] = useState<Record<string, string>>({});
  const [runs, setRuns] = useState<PrecostResponse[]>([]);
  const [error, setError] = useState<{ message: string; forbidden: boolean } | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const pickers = conflicts(data.data.candidates);

  const invalid =
    !/^\d+$/.test(form.min_quantity) ||
    Number(form.min_quantity) < 1 ||
    !DECIMAL.test(form.fixed_costs) ||
    !DECIMAL.test(form.variable_cost_per_unit) ||
    !/^[A-Z]{3}$/.test(form.currency) ||
    (form.target_price !== "" && !DECIMAL.test(form.target_price));

  async function submit(e: { preventDefault: () => void }) {
    e.preventDefault();
    if (invalid) return;
    setIsBusy(true);
    setError(null);
    try {
      const res = await rfqApi.precost(
        {
          rfq_id: rfqId,
          inputs: {
            item: data.data.item,
            min_quantity: Number(form.min_quantity),
            fixed_costs: form.fixed_costs,
            variable_cost_per_unit: form.variable_cost_per_unit,
            currency: form.currency,
            target_price: form.target_price === "" ? null : form.target_price,
          },
          breakdown: null,
          resolved,
        },
        { team },
      );
      setRuns((r) => [res, ...r].slice(0, 5));
    } catch (err) {
      setError({ message: describeError(err), forbidden: isForbidden(err) });
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
        <ul className="divide-y divide-line" data-testid="cost-candidates">
          {data.data.candidates.map((c) => (
            <Candidate key={c.field} c={c} rfqId={rfqId} />
          ))}
        </ul>
      </Panel>

      <div className="flex flex-col gap-4">
        <Panel title="계산" labelId="calc-title">
          <form onSubmit={submit} className="grid gap-3 p-4 md:grid-cols-2">
            {field("min_quantity", "최소 수량 (개)")}
            {field("fixed_costs", "고정비 (금형비 + 개발비)")}
            {field("variable_cost_per_unit", "단위당 변동비", "RFQ에 없으면 직접 입력")}
            {field("currency", "통화")}
            {field("target_price", "목표가 (단위당)", "비워 두면 기준선 없이 계산")}
            {pickers.length > 0 && (
              <fieldset className="flex flex-col gap-3 rounded-control border border-line p-3 md:col-span-2" data-testid="conflict-pickers">
                <legend className="px-1 text-label font-semibold text-ink-muted">파일마다 다른 값 — 하나를 고르세요</legend>
                {pickers.map((c) => {
                  const issueId = c.issue_id ?? "";
                  return (
                    <div key={issueId} className="flex flex-col gap-1.5" role="radiogroup" aria-label={`${c.label} 값 고르기`}>
                      <span className="text-dense font-medium text-ink">
                        {c.label}
                        {c.unit ? ` (${c.unit})` : ""}
                      </span>
                      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                        {[null, ...c.alternatives.map((a) => a.value)].map((v) => (
                          <label key={v ?? "none"} className="flex items-center gap-1.5 text-dense text-ink">
                            <input
                              type="radio"
                              name={`resolve-${issueId}`}
                              checked={(resolved[issueId] ?? null) === v}
                              data-testid={`resolve-${issueId}-${v ?? "none"}`}
                              onChange={() =>
                                setResolved((prev) => {
                                  const next = { ...prev };
                                  if (v === null) delete next[issueId];
                                  else next[issueId] = v;
                                  return next;
                                })
                              }
                            />
                            {v ?? "아직 정하지 않음"}
                          </label>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </fieldset>
            )}
            <p className="text-label text-ink-muted md:col-span-2">
              단위 원가 = 고정비 ÷ 최소 수량 + 단위당 변동비 · 소수 넷째 자리 반올림
            </p>
            <div className="flex flex-wrap items-center gap-2 md:col-span-2">
              <Button type="submit" variant="primary" disabled={invalid || isBusy} data-testid="calc">
                <IconCalc size={14} /> {runs.length ? "판정 다시 계산" : "계산"}
              </Button>
              {invalid && <span className="text-label text-ink-muted">모든 값을 숫자로 넣어 주세요.</span>}
            </div>
          </form>
          {error && (error.forbidden ? <ForbiddenState message={error.message} /> : <ErrorState message={error.message} />)}
        </Panel>

        <Panel title="결과" labelId="result-title" provenance={latest?.provenance}>
          {!latest && <EmptyState title="아직 계산하지 않았습니다" />}
          {latest && <Result run={latest} sourceProvenance={data.provenance} />}
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
                    <th scope="col" className="px-3 py-2">판정</th>
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
                      <td className="px-3 py-2">{DECISION[r.data.decision.status].label}</td>
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

function Candidate({ c, rfqId }: { c: CostInputCandidate; rfqId: string }) {
  const verified = c.evidence?.citation_status === "verified";
  return (
    <li className="flex flex-col gap-1.5 px-4 py-3" data-testid={`candidate-${c.field}`}>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-dense font-semibold text-ink">{c.label}</span>
        <span className={cx("text-label", c.source === "rfq" ? "text-ink-muted" : "font-semibold text-ink")}>
          {SOURCE_LABEL[c.source]}
        </span>
        <span className="ml-auto text-body font-semibold text-ink tabular">
          {c.value !== null ? (
            `${c.value}${c.unit ? ` ${c.unit}` : ""}`
          ) : c.alternatives.length > 1 ? (
            <span className="text-label font-medium text-warn">파일마다 다름</span>
          ) : (
            <span className="text-label font-medium text-warn">값 없음</span>
          )}
        </span>
      </div>
      {c.note && <p className="text-label text-ink-muted">{c.note}</p>}
      {c.evidence && (
        <div className="flex flex-wrap gap-1">
          {c.evidence.finding.citations.map((l, i) => (
            <CitationChip key={locusKey(l, i)} locus={l} verified={verified} />
          ))}
        </div>
      )}
      {c.alternatives.length > 0 && (
        <ul className="flex flex-col gap-1">
          {c.alternatives.map((a) => (
            <li key={a.value} className="flex flex-wrap items-center gap-1.5 text-label text-ink">
              <strong className="font-semibold tabular">{a.value}</strong>
              {a.evidence.finding.citations.map((l, i) => (
                <CitationChip key={locusKey(l, i)} locus={l} verified={a.evidence.citation_status === "verified"} />
              ))}
            </li>
          ))}
        </ul>
      )}
      {c.issue_id && (
        <Link
          href={`/rfq/${encodeURIComponent(rfqId)}/consistency`}
          className="self-start text-label text-accent hover:underline"
          data-testid={`candidate-issue-${c.field}`}
        >
          정합성 이슈 {c.issue_id} 보기
        </Link>
      )}
    </li>
  );
}

const DECISION: Record<DecisionStatus, { label: string; className: string; icon: ReactNode }> = {
  go: { label: "Go", className: "border-ok bg-ok-tint text-ok", icon: <IconCheck size={14} /> },
  no_go: { label: "No-Go", className: "border-danger bg-danger-tint text-danger", icon: <IconWarn size={14} /> },
  hold: { label: "보류", className: "border-line-control bg-surface text-ink", icon: <IconPause size={14} /> },
  baseline_undefined: { label: "기준 미정", className: "border-line-control bg-surface text-ink-muted", icon: null },
};

function Result({ run, sourceProvenance }: { run: PrecostResponse; sourceProvenance: CostInputsResponse["provenance"] }) {
  const d = run.data;
  const cur = d.inputs.currency;
  const decision = DECISION[d.decision.status];
  const ranged = d.range.low !== d.range.high;
  return (
    <div className="flex flex-col gap-4 p-4" data-testid="precost-result">
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-0.5 rounded-control bg-canvas p-3">
          <span className="text-label text-ink-muted">단위당 고정비</span>
          <span className="text-heading font-semibold text-ink tabular">
            {d.fixed_cost_per_unit} <span className="text-label">{cur}</span>
          </span>
        </div>
        <div className="flex flex-col gap-0.5 rounded-control bg-accent-tint p-3">
          <span className="text-label text-accent">단위 원가</span>
          <span className="text-title font-semibold text-ink tabular" data-testid="unit-cost">
            {d.unit_cost} <span className="text-label">{cur}</span>
          </span>
          {ranged && (
            <span className="text-label text-ink-muted tabular">
              범위 {d.range.low} ~ {d.range.high}
            </span>
          )}
        </div>
      </div>

      <section
        aria-labelledby="decision-title"
        className="flex flex-col gap-2 rounded-control border border-line p-3"
        data-testid="decision"
        data-status={d.decision.status}
      >
        <div className="flex flex-wrap items-center gap-2">
          <h3 id="decision-title" className="text-label font-semibold text-ink-muted">
            입찰 판단 (Go / No-Go)
          </h3>
          <span
            className={cx("inline-flex items-center gap-1 rounded-chip border px-2 py-0.5 text-dense font-semibold", decision.className)}
            data-testid="decision-status"
          >
            {decision.icon}
            {decision.label}
          </span>
        </div>
        <p className="text-dense text-ink">{d.decision.note}</p>
        {d.decision.reasons.length > 0 && (
          <ul className="flex list-disc flex-col gap-0.5 pl-5 text-label text-ink">
            {d.decision.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        <p className="flex flex-wrap gap-x-3 text-label text-ink-muted tabular">
          {d.decision.target_price !== null && <span>목표가 {d.decision.target_price} {cur}</span>}
          {d.decision.gap !== null && <span>목표가 − 원가 상한 {d.decision.gap}</span>}
        </p>
        <p className="text-label text-ink-muted" data-testid="decision-rule">
          판정 규칙 <code className="font-semibold text-ink">{d.decision.rule}</code>이 계산한 결과입니다. 규칙(data/rules)을 그대로
          적용했을 뿐 시스템의 권고가 아니며, 견적 여부는 원가 담당이 정합니다.
        </p>
      </section>

      {d.scenarios.length > 0 && (
        <table className="w-full text-left text-dense tabular" data-testid="scenarios">
          <caption className="pb-1 text-left text-label font-semibold text-ink-muted">충돌 값별 단위 원가</caption>
          <tbody>
            {d.scenarios.map((s) => (
              <tr key={s.label} className="border-t border-line">
                <td className="py-1.5 pr-3 text-ink">{s.label}</td>
                <td className="py-1.5 text-right font-semibold text-ink">
                  {s.unit_cost} {cur}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {d.yearly.length > 0 && (
        <table className="w-full text-left text-dense tabular" data-testid="yearly">
          <caption className="pb-1 text-left text-label font-semibold text-ink-muted">연도별 목표가 (연간 인하 적용)</caption>
          <tbody>
            {d.yearly.map((y) => (
              <tr key={y.year} className="border-t border-line">
                <td className="py-1.5 pr-3 text-ink">{y.year}</td>
                <td className="py-1.5 text-right text-ink">
                  {y.price} {cur}
                </td>
                <td className="py-1.5 pl-3 text-right text-label">
                  {y.below_cost ? <span className="font-semibold text-danger">원가보다 낮음</span> : <span className="text-ink-muted">원가 이상</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="flex flex-wrap items-center gap-2 text-label text-ink-muted">
        입력 후보 출처 <ProvenanceBadge provenance={sourceProvenance} /> · 계산식은 SPEC §8 그대로입니다.
      </p>
    </div>
  );
}
