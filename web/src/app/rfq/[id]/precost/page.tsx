"use client";
/**
 * Pre-cost Go/No-Go (SPEC §8). Steps ①–⑤ come from the backend's setup: the RFQ
 * type, the knockout questions (answered here as yes / no / unknown), the cost
 * inputs the RFQ states with citations, and cost items the price table can
 * already price. The user confirms the inputs and the cost items; the backend
 * then computes the lifetime minimum quote, the shock range, the score and the
 * verdict (⑥–⑨). This screen never computes a number and adds no
 * recommendation of its own.
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
  Skeleton,
  cx,
  locusKey,
} from "@/components/ui/primitives";
import {
  ITEM_CATEGORIES,
  type CompanyResponse,
  type CostInputCandidate,
  type DecisionStatus,
  type ItemCategory,
  type PrecostInputs,
  type PrecostResponse,
  type PrecostSetupResponse,
  type PriceRow,
  type RequirementCheck,
  type RfqLine,
  type VerifiedFinding,
} from "@/lib/rfq/contract";
import {
  CATEGORY_LABEL,
  currencyOptions,
  fractionToPercent,
  isDecimal,
  isInt,
  percentToFraction,
  unitOptions,
} from "@/lib/rfq/precost";
import { describeError, isForbidden, rfqApi } from "@/lib/rfq/provider";
import { useSession } from "@/lib/rfq/session";
import { useResource } from "@/lib/rfq/use-resource";

const RESTRICTED_NOTE = "이 팀은 금액 관련 정보를 볼 수 없습니다. 금액 열람 그룹(시연용 가정: 영업·구매·원가)으로 팀을 바꾸면 볼 수 있습니다.";

type Answer = "yes" | "no" | "unknown";
type Setup = PrecostSetupResponse["data"];
type Company = CompanyResponse["data"];

async function load(rfqId: string, team: string | null, signal: AbortSignal) {
  const [setup, company] = await Promise.all([
    rfqApi.precostSetup(rfqId, { team }, signal),
    rfqApi.company({ team }, signal),
  ]);
  return { setup: setup.data, company: company.data };
}

export default function PrecostPage() {
  const { id } = useParams<{ id: string }>();
  const rfqId = decodeURIComponent(id);
  const session = useSession();
  const { restricted, ready, error: metaError } = useTeamGroup();
  const loaded = useResource(ready && !restricted ? `precost:${rfqId}:${session.team}` : null, (s) =>
    load(rfqId, session.team, s),
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
    <div className="mx-auto flex max-w-7xl flex-col gap-4 p-3 md:p-6">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-1">
        <div className="min-w-0 flex-1">
          <h1 className="text-title font-semibold text-ink">사전원가 Go / No-Go</h1>
          <p className="text-body text-ink-muted">
            RFQ 유형과 1차 넉아웃을 확인하고, RFQ에 없는 값만 채우면 최소 견적·안전 견적·점수·판정을 냅니다. 단가표와 회사
            설정은 첫 화면의 기본 설정(톱니바퀴)에서 관리합니다. AI는 수치를 만들지 않습니다.
          </p>
        </div>
      </div>
      {metaError && <ErrorState message={`팀 정보를 불러오지 못했습니다. ${metaError}`} />}
      {!metaError && (!ready || loaded.status === "loading") && <Skeleton lines={6} />}
      {loaded.status === "error" &&
        (loaded.forbidden ? <ForbiddenState message={loaded.error} /> : <ErrorState message={loaded.error} />)}
      {loaded.status === "ok" && (
        <Workbench setup={loaded.data.setup} company={loaded.data.company} team={session.team} rfqId={rfqId} />
      )}
    </div>
  );
}

// ── form state ──────────────────────────────────────────────────────────────
type ItemRow = {
  key: string;
  label: string;
  category: ItemCategory;
  quantity: string;
  unit: string;
  /** Empty: the user does not know it yet. The calculation then stops instead of using 0. */
  unit_price: string;
  price_id: string | null;
};
type VolumeRow = { key: string; year: string; quantity: string };
/** One line item priced on its own (line-item pricing). */
type LineRow = {
  key: string;
  item: string;
  target: string;
  /** Price-sheet group and page, when the line came from the RFQ. */
  group?: string | null;
  page?: number;
  volumes: VolumeRow[];
  items: ItemRow[];
  /** Process requirements of the RFQ this line needs. */
  processes: string[];
  included: boolean;
};
const AWARD_RFQ: Record<string, string> = {
  split: "품목별 낙찰",
  all_or_none: "일괄 낙찰",
  either: "발주처가 선택 → 품목별로 판정",
  none: "명시 없음 → 일괄로 가정",
};
type AwardChoice = "rfq" | "split" | "all_or_none";
/** Line-item pricing as entered, or null when the RFQ is priced as one item. */
type LineSpec = { lines: LineRow[]; award: AwardChoice; shared: string } | null;
type Form = {
  currency: string;
  target_price: string;
  reduction: string; // percent
  starts: string;
  payment_days: string;
  tooling_amount: string;
  tooling_payment: "lump_sum" | "amortized" | "deferred";
  deferred_years: string;
  material_indexed: "auto" | "yes" | "no";
  fx_adjusted: "auto" | "yes" | "no";
  capacity: string; // percent
  strategic: string;
};

let nextKey = 0;
const newKey = () => `k${++nextKey}`;
const candidate = (setup: Setup, field: string) => setup.cost_inputs.candidates.find((c) => c.field === field);

function initialForm(setup: Setup, company: Company): Form {
  const value = (f: string) => candidate(setup, f)?.value ?? "";
  const rate = setup.cost_inputs.annual_reduction_rate;
  return {
    currency: value("currency") || company.settings.home_currency,
    target_price: value("target_price"),
    reduction: rate ? fractionToPercent(rate) : "0",
    starts: String(setup.cost_inputs.reduction_starts_at_index),
    payment_days: value("payment_days"),
    tooling_amount: "0",
    tooling_payment: "amortized",
    deferred_years: "0",
    material_indexed: "auto",
    fx_adjusted: "auto",
    capacity: "",
    strategic: "",
  };
}

function initialVolumes(setup: Setup): VolumeRow[] {
  if (setup.cost_inputs.volumes.length > 0) {
    return setup.cost_inputs.volumes.map((v) => ({ key: newKey(), year: String(v.year), quantity: String(v.quantity) }));
  }
  const total = candidate(setup, "volume")?.value ?? "";
  return [{ key: newKey(), year: String(new Date().getFullYear() + 1), quantity: total }];
}

function initialItems(setup: Setup): ItemRow[] {
  const suggested = setup.suggested_items.map((s) => ({
    key: newKey(),
    label: s.label,
    category: s.category,
    quantity: s.quantity === "0" ? "" : s.quantity,
    unit: s.unit,
    unit_price: s.unit_price,
    price_id: s.price_id,
  }));
  return suggested.length > 0
    ? suggested
    : [{ key: newKey(), label: "", category: "material", quantity: "", unit: "kg", unit_price: "", price_id: null }];
}

/** Lines priced from the RFQ's price sheet: name and printed quantity; the user adds the purchase price. */
function linesFromRfq(rows: RfqLine[], year: string, processes: string[]): LineRow[] {
  return rows.map((r) => ({
    key: newKey(),
    item: r.part ? `${r.name} (${r.part})` : r.name,
    target: "",
    volumes: [{ key: newKey(), year, quantity: String(r.quantity) }],
    items: [{ key: newKey(), label: "매입가", category: "purchased", quantity: "1", unit: "개", unit_price: "", price_id: null }],
    processes,
    included: true,
    group: r.group,
    page: r.page,
  }));
}

const SWITCH = { auto: null, yes: true, no: false } as const;
const STARTS: Record<string, string> = { "0": "첫해부터", "1": "2년차부터", "2": "3년차부터" };

type FieldSpec = {
  id: string;
  label: string;
  hint?: string;
  stated: boolean;
  /** Read from a sentence that does not state it plainly: shown as inferred, with the reason. */
  inferred?: string | null;
  evidence: VerifiedFinding | null;
  show: () => ReactNode;
  edit: () => ReactNode;
};

function volumesProblem(rows: VolumeRow[], where: string): string | null {
  return rows.length === 0 || rows.some((v) => !isInt(v.year) || !isInt(v.quantity)) ? `${where}연도별 물량을 정수로 넣으세요.` : null;
}

function itemsProblem(rows: ItemRow[], where: string): string | null {
  if (rows.length === 0) return `${where}원가 항목을 하나 이상 넣으세요.`;
  for (const [i, row] of rows.entries()) {
    if (!row.label.trim()) return `${where}원가 항목 ${i + 1}행의 이름을 넣으세요.`;
    if (!isDecimal(row.quantity)) return `${where}'${row.label}'의 수량을 넣으세요.`;
    if (!row.unit_price.trim())
      return `${where}'${row.label}'의 단가를 모릅니다. 단가를 넣기 전에는 계산하지 않습니다(0으로 대신하지 않음).`;
    if (!isDecimal(row.unit_price)) return `${where}'${row.label}'의 단가는 숫자로 넣으세요.`;
  }
  return null;
}

const toVolumes = (rows: VolumeRow[]) => rows.map((v) => ({ year: Number(v.year), quantity: Number(v.quantity) }));
const toItems = (rows: ItemRow[]) =>
  rows.map((r) => ({
    label: r.label.trim(),
    category: r.category,
    quantity: r.quantity.trim(),
    unit: r.unit.trim() || "개",
    unit_price: r.unit_price.trim(),
    price_id: r.price_id,
  }));

/** The request body, or the first problem that stops the calculation. */
function build(
  form: Form,
  volumes: VolumeRow[],
  items: ItemRow[],
  answers: Record<string, Answer>,
  item: string,
  spec: LineSpec,
  processValues: string[],
): { inputs: PrecostInputs } | { problem: string } {
  if (!/^[A-Z]{3}$/.test(form.currency)) return { problem: "통화는 영문 대문자 3자로 넣으세요." };
  if (!isDecimal(form.reduction)) return { problem: "연간 인하율은 숫자(%)로 넣으세요." };
  if (spec) {
    if (spec.lines.length === 0) return { problem: "품목을 하나 이상 넣으세요." };
    if (!isDecimal(spec.shared)) return { problem: "공통비는 숫자로 넣으세요(없으면 0)." };
    for (const [i, line] of spec.lines.entries()) {
      const where = `품목 ${i + 1}: `;
      if (!line.item.trim()) return { problem: `${where}이름을 넣으세요.` };
      if (line.target && !isDecimal(line.target)) return { problem: `${where}목표가는 숫자로 넣으세요.` };
      const found = volumesProblem(line.volumes, where) ?? itemsProblem(line.items, where);
      if (found) return { problem: found };
    }
  } else {
    if (form.target_price && !isDecimal(form.target_price)) return { problem: "목표가는 숫자로 넣으세요." };
    const found = volumesProblem(volumes, "") ?? itemsProblem(items, "");
    if (found) return { problem: found };
  }
  if (form.payment_days && !isInt(form.payment_days)) return { problem: "결제 기한은 일수로 넣으세요." };
  if (!isDecimal(form.tooling_amount)) return { problem: "금형비는 숫자로 넣으세요(없으면 0)." };
  if (form.capacity && !isDecimal(form.capacity)) return { problem: "가동률은 숫자(%)로 넣으세요." };
  if (form.strategic && (!isInt(form.strategic) || Number(form.strategic) > 100))
    return { problem: "전략 점수는 0~100 사이 정수로 넣으세요." };
  const lines = spec
    ? {
        lines: spec.lines.map((l) => ({
          item: l.item.trim(),
          target_price: l.target.trim() || null,
          volumes: toVolumes(l.volumes),
          items: toItems(l.items),
          // Every process ticked: the line needs all of them, which is what null says.
          processes: l.processes.length === processValues.length ? null : l.processes,
          included: l.included,
        })),
        award: spec.award === "rfq" ? null : spec.award,
        shared_costs: spec.shared.trim(),
      }
    : {};
  return {
    inputs: {
      item,
      currency: form.currency,
      target_price: spec ? null : form.target_price.trim() || null,
      volumes: spec ? [] : toVolumes(volumes),
      annual_reduction_rate: percentToFraction(form.reduction),
      reduction_starts_at_index: Number(form.starts),
      payment_days: form.payment_days ? Number(form.payment_days) : null,
      items: spec ? [] : toItems(items),
      tooling: {
        amount: form.tooling_amount.trim(),
        payment: form.tooling_payment,
        deferred_years: form.tooling_payment === "deferred" ? Number(form.deferred_years) || 0 : 0,
      },
      material_indexed: SWITCH[form.material_indexed],
      fx_adjusted: SWITCH[form.fx_adjusted],
      capacity_utilization: form.capacity ? percentToFraction(form.capacity) : null,
      strategic: form.strategic ? Number(form.strategic) : null,
      answers,
      ...lines,
    },
  };
}

/** One picker per unresolved consistency issue: the candidate that lists the disagreeing values. */
function conflicts(candidates: CostInputCandidate[]): CostInputCandidate[] {
  const seen = new Set<string>();
  return candidates.filter((c) => {
    if (!c.issue_id || c.alternatives.length < 2 || seen.has(c.issue_id)) return false;
    seen.add(c.issue_id);
    return true;
  });
}

const input = "min-h-9 w-full rounded-control border border-line-control bg-surface px-2 text-dense text-ink";

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-label font-semibold text-ink-muted">{label}</span>
      {children}
      {hint && <span className="text-label text-ink-muted">{hint}</span>}
    </label>
  );
}

function Step({ n, title, children, aside }: { n: string; title: string; children: ReactNode; aside?: ReactNode }) {
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

// ── the workbench ───────────────────────────────────────────────────────────
function Workbench({ setup, company, team, rfqId }: { setup: Setup; company: Company; team: string | null; rfqId: string }) {
  const [form, setFormState] = useState<Form>(() => initialForm(setup, company));
  const [volumes, setVolumes] = useState<VolumeRow[]>(() => initialVolumes(setup));
  const [items, setItems] = useState<ItemRow[]>(() => initialItems(setup));
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [resolved, setResolved] = useState<Record<string, string>>({});
  const prices: PriceRow[] = company.prices;
  const [editing, setEditing] = useState<Set<string>>(() => new Set());
  const [result, setResult] = useState<PrecostResponse | null>(null);
  const [error, setError] = useState<{ message: string; forbidden: boolean } | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const processValues = setup.requirements.filter((r) => r.requirement === "process").map((r) => r.value);
  const sheet = setup.lines.items;
  const sheetYear = initialVolumes(setup)[0]?.year ?? String(new Date().getFullYear() + 1);
  const [byLine, setByLine] = useState(setup.award.mode === "split" || setup.award.mode === "either" || sheet.length > 0);
  const [award, setAward] = useState<AwardChoice>("rfq");
  const [shared, setShared] = useState("0");
  const [lines, setLines] = useState<LineRow[]>(() =>
    sheet.length > 0
      ? linesFromRfq(sheet, sheetYear, processValues)
      : [{ key: newKey(), item: setup.cost_inputs.item || "품목 1", target: "", volumes: initialVolumes(setup), items: initialItems(setup), processes: processValues, included: true }],
  );
  // "Either": the customer chooses after the bids, so every line is judged on its own as under a split award.
  const rfqAward = setup.award.mode === "either" ? "split" : (setup.award.mode ?? "all_or_none");
  const effectiveAward = award === "rfq" ? rfqAward : award;

  const setForm = (patch: Partial<Form>) => setFormState((f) => ({ ...f, ...patch }));
  const { departments } = useTeamGroup();
  const departmentLabel = (id: string) => departments.find((d) => d.id === id)?.label ?? id;
  // Distribution RFQs are priced per unit of purchases (e.g. 100 USD worth), not per piece.
  const per = setup.classification.type === "distribution" ? "단위" : "개";
  const asked = setup.requirements.filter((r) => r.status === "ask");
  const settled = setup.requirements.filter((r) => r.status !== "ask");
  const unanswered = asked.filter((r) => !answers[r.key]);
  const pickers = conflicts(setup.cost_inputs.candidates);
  const built = build(form, volumes, items, answers, setup.cost_inputs.item, byLine ? { lines, award, shared } : null, processValues);
  const problem = "problem" in built ? built.problem : unanswered.length > 0 ? `넉아웃 질문 ${unanswered.length}개에 답하세요.` : null;

  async function submit() {
    if (!("inputs" in built) || problem) return;
    setIsBusy(true);
    setError(null);
    try {
      setResult(await rfqApi.precost({ rfq_id: rfqId, inputs: built.inputs, resolved, remember: true }, { team }));
    } catch (err) {
      setError({ message: describeError(err), forbidden: isForbidden(err) });
    } finally {
      setIsBusy(false);
    }
  }

  const patchItem = (key: string, patch: Partial<ItemRow>) =>
    setItems((list) => list.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const toggle = (id: string) =>
    setEditing((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // ③/④: each RFQ condition is either stated in the RFQ (shown with its citation, editable on request)
  // or missing (an input in ④). A value read from a sentence that does not state it plainly is filled
  // in, marked as inferred, with that sentence: the user confirms it.
  const evidenceOf = (field: string) => candidate(setup, field)?.evidence ?? null;
  const statedIn = (field: string) => candidate(setup, field)?.value != null;
  const inferredIn = (field: string) => {
    const c = candidate(setup, field);
    return c?.source === "inferred" ? c.note : null;
  };
  const clause = (id: string) => setup.pass_through.find((p) => p.id === id)?.evidence ?? null;
  const switchLabel = (value: Form["material_indexed"], found: boolean, shock: string) =>
    value === "yes" || (value === "auto" && found) ? `있음 · ${shock} 충격 제외` : `없음 · ${shock} 충격 반영`;
  const switchEdit = (key: "material_indexed" | "fx_adjusted") => (
    <select className={cx(input, "max-w-60")} value={form[key]} onChange={(e) => setForm({ [key]: e.target.value as Form[typeof key] })}>
      <option value="auto">RFQ대로</option>
      <option value="yes">있음</option>
      <option value="no">없음</option>
    </select>
  );
  const fields: FieldSpec[] = [
    {
      id: "currency",
      label: "통화",
      stated: statedIn("currency"),
      inferred: inferredIn("currency"),
      evidence: evidenceOf("currency"),
      show: () => form.currency,
      edit: () => (
        <select className={cx(input, "max-w-60")} value={form.currency} data-testid="input-currency"
          onChange={(e) => setForm({ currency: e.target.value })}>
          {currencyOptions(form.currency, company.settings.home_currency).map((c) => (
            <option key={c.code} value={c.code}>
              {c.label}
            </option>
          ))}
        </select>
      ),
    },
    {
      id: "target_price",
      label: `목표가 (${per}당)`,
      hint: "RFQ에 목표가가 없으면 비워 두세요. 판정 대신 최소 입찰가만 냅니다.",
      stated: statedIn("target_price"),
      evidence: evidenceOf("target_price"),
      show: () => (form.target_price ? `${form.target_price} ${form.currency}` : "없음"),
      edit: () => (
        <input className={cx(input, "max-w-60 tabular")} value={form.target_price} inputMode="decimal" data-testid="input-target_price"
          onChange={(e) => setForm({ target_price: e.target.value.trim() })} />
      ),
    },
    {
      id: "payment_days",
      label: "결제 기한 (일)",
      hint: "모르면 비워 두세요.",
      stated: statedIn("payment_days"),
      inferred: inferredIn("payment_days"),
      evidence: evidenceOf("payment_days"),
      show: () => (form.payment_days ? `${form.payment_days}일` : "없음"),
      edit: () => (
        <input className={cx(input, "max-w-60 tabular")} value={form.payment_days} inputMode="numeric" data-testid="input-payment_days"
          onChange={(e) => setForm({ payment_days: e.target.value.trim() })} />
      ),
    },
    {
      id: "reduction",
      label: "연간 단가 인하율",
      hint: "매년 단가를 깎아 달라는 조건이 없으면 0입니다.",
      stated: setup.cost_inputs.annual_reduction_rate !== null,
      evidence: evidenceOf("annual_reduction_rate"),
      show: () => `${form.reduction || 0}% · ${STARTS[form.starts]}`,
      edit: () => (
        <div className="flex flex-wrap items-center gap-2">
          <input className={cx(input, "w-24 tabular")} value={form.reduction} inputMode="decimal" data-testid="input-reduction"
            aria-label="연간 단가 인하율 (%)" onChange={(e) => setForm({ reduction: e.target.value.trim() })} />
          <span className="text-label text-ink-muted">%</span>
          <select className={cx(input, "w-32")} value={form.starts} aria-label="인하 시작" onChange={(e) => setForm({ starts: e.target.value })}>
            {Object.entries(STARTS).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
      ),
    },
    {
      id: "volumes",
      label: "연도별 물량",
      hint:
        per === "단위"
          ? "유통 RFQ는 품목이 많아 '단위'로 넣습니다. 예: 매입 100 USD어치를 1단위로 잡으면 연간 예상 매입액 ÷ 100."
          : "RFQ의 계획 물량(개)을 연도별로 넣으세요.",
      stated: setup.cost_inputs.volumes.length > 0,
      evidence: evidenceOf("volume"),
      show: () => volumes.map((v) => `${v.year} ${Number(v.quantity || 0).toLocaleString("ko-KR")}`).join(" · "),
      edit: () => <VolumesEditor rows={volumes} setRows={setVolumes} per={per} />,
    },
    {
      id: "indexation",
      label: "원자재 연동",
      stated: true,
      evidence: clause("indexation"),
      show: () => switchLabel(form.material_indexed, clause("indexation") !== null, "원자재"),
      edit: () => switchEdit("material_indexed"),
    },
    {
      id: "fx_adjustment",
      label: "환율 조정",
      stated: true,
      evidence: clause("fx_adjustment"),
      show: () => switchLabel(form.fx_adjusted, clause("fx_adjustment") !== null, "환율"),
      edit: () => switchEdit("fx_adjusted"),
    },
  ];
  // Per line item, the target and the volumes are entered on each line.
  const shown = byLine ? fields.filter((f) => f.id !== "target_price" && f.id !== "volumes") : fields;
  const stated = shown.filter((f) => f.stated);
  const missing = shown.filter((f) => !f.stated);
  const hints = (["material", "net_weight", "thickness", "incoterm"] as const)
    .map((field) => candidate(setup, field))
    .filter((c): c is CostInputCandidate => !!c && c.value !== null)
    .map((c) => `${c.label} ${c.value}${c.unit ? ` ${c.unit}` : ""}`);

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
      <Panel title="입력" labelId="inputs-title" provenance={{ precost: "real" }}>
        <Step n="①" title={`RFQ 유형: ${setup.classification.label}`}>
          <p className="text-label text-ink-muted" data-testid="rfq-type" data-type={setup.classification.type}>
            {setup.classification.note}
          </p>
        </Step>

        <Step n="②" title="1차 넉아웃: 자격·계약 조건" aside={<KnockoutCount asked={asked.length} settled={settled.length} />}>
          {setup.requirements.length === 0 && (
            <p className="text-label text-ink-muted">RFQ에서 인증·공정·자격·책임·결제 조건을 찾지 못했습니다.</p>
          )}
          <ul className="flex flex-col gap-2" data-testid="knockout-questions">
            {asked.map((r) => (
              <Question
                key={r.key}
                r={r}
                department={departmentLabel(r.department)}
                value={answers[r.key]}
                onChange={(a) => setAnswers((p) => ({ ...p, [r.key]: a }))}
              />
            ))}
            {settled.map((r) => (
              <li key={r.key} className="flex flex-wrap items-center gap-2 text-label text-ink-muted">
                <span className={cx("font-semibold", r.status === "pass" ? "text-ok" : "text-danger")}>
                  {r.status === "pass" ? "충족" : "불충족"}
                </span>
                <span className="text-ink">{r.label}: {r.value}</span>
                <span>({r.source === "profile" ? "회사 설정" : "저장된 답"})</span>
                <Cite evidence={r.evidence} />
              </li>
            ))}
          </ul>
          <p className="text-label text-ink-muted">
            &lsquo;모름&rsquo;은 탈락시키지 않지만 Go를 조건부로 낮춥니다. 사실 질문의 예/아니오는 다음 RFQ에도 씁니다.
          </p>
        </Step>

        <Step n="③" title="RFQ에서 읽은 값" aside={<span className="text-label text-ink-muted">확인만 하세요 · 다르면 수정</span>}>
          <ul className="flex flex-col divide-y divide-line rounded-control border border-line" data-testid="rfq-values">
            {stated.length === 0 && <li className="px-3 py-2 text-label text-ink-muted">RFQ에서 읽은 원가 조건이 없습니다.</li>}
            {stated.map((f) => (
              <li key={f.id} className="flex flex-col gap-1.5 px-3 py-2" data-testid={`rfq-value-${f.id}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="w-28 shrink-0 text-label font-semibold text-ink-muted">{f.label}</span>
                  <span className="text-dense font-semibold text-ink tabular">{f.show()}</span>
                  {f.inferred && (
                    <span className="rounded-control border border-warn bg-warn-tint px-1.5 text-label font-semibold text-warn"
                      data-testid={`inferred-${f.id}`}>
                      추정
                    </span>
                  )}
                  <Cite evidence={f.evidence} />
                  <button type="button" className="ml-auto text-label text-accent hover:underline" onClick={() => toggle(f.id)}
                    data-testid={`edit-${f.id}`}>
                    {editing.has(f.id) ? "닫기" : "수정"}
                  </button>
                </div>
                {f.inferred && <p className="text-label text-ink-muted sm:pl-30">{f.inferred}</p>}
                {editing.has(f.id) && <div className="pl-0 sm:pl-30">{f.edit()}</div>}
              </li>
            ))}
          </ul>
        </Step>

        <Step n="④" title="낙찰 방식·입찰 단위">
          <p className="flex flex-wrap items-center gap-2 text-label text-ink-muted" data-testid="award-mode" data-mode={setup.award.mode ?? "none"}>
            <span className="font-semibold text-ink">RFQ: {setup.award.label}</span>
            <Cite evidence={setup.award.evidence} />
          </p>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5" role="radiogroup" aria-label="입찰 단위">
            <label className="flex items-center gap-1.5 text-dense text-ink">
              <input type="radio" name="bid-unit" checked={!byLine} onChange={() => setByLine(false)} data-testid="bid-whole" />
              RFQ 전체를 한 품목으로 계산
            </label>
            <label className="flex items-center gap-1.5 text-dense text-ink">
              <input type="radio" name="bid-unit" checked={byLine} onChange={() => setByLine(true)} data-testid="bid-lines" />
              품목별로 계산
            </label>
          </div>
          {byLine && (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field
                label="낙찰 방식"
                hint={effectiveAward === "split" ? "품목마다 판정하고, 입찰할 품목만 골라 합계를 냅니다." : "모든 품목을 입찰하고 전체 합계로 판정합니다."}
              >
                <select className={input} value={award} data-testid="input-award" onChange={(e) => setAward(e.target.value as AwardChoice)}>
                  <option value="rfq">
                    RFQ대로 ({AWARD_RFQ[setup.award.mode ?? "none"]})
                  </option>
                  <option value="split">품목별 낙찰</option>
                  <option value="all_or_none">일괄 낙찰</option>
                </select>
              </Field>
              <Field label={`공통비 (총액, ${form.currency})`} hint="여러 품목이 함께 쓰는 금형·셋업비. 입찰하는 품목의 물량 비율로 나눕니다.">
                <input className={cx(input, "tabular")} value={shared} inputMode="decimal" data-testid="input-shared"
                  onChange={(e) => setShared(e.target.value.trim())} />
              </Field>
            </div>
          )}
          {byLine && sheet.length > 0 && (
            <SheetPicker sheet={sheet} total={setup.lines.total} limit={setup.lines.limit}
              apply={(rows) =>
                // Lines added by hand are kept: the sheet reading missed them.
                setLines((list) => [...linesFromRfq(rows, sheetYear, processValues), ...list.filter((l) => l.group === MANUAL)])
              } />
          )}
        </Step>

        <Step n="⑤" title="추가로 필요한 값">
          {missing.length > 0 && (
            <div className="flex flex-col gap-3" data-testid="missing-values">
              <p className="text-label text-ink-muted">RFQ에 적혀 있지 않은 값입니다. 직접 넣어 주세요.</p>
              {missing.map((f) => (
                <Field key={f.id} label={f.label} hint={f.hint}>
                  {f.edit()}
                </Field>
              ))}
            </div>
          )}
          {pickers.length > 0 && (
            <fieldset className="flex flex-col gap-3 rounded-control border border-line p-3" data-testid="conflict-pickers">
              <legend className="px-1 text-label font-semibold text-ink-muted">파일마다 다른 값: 고르지 않으면 경우마다 계산합니다</legend>
              <p className="text-label text-ink-muted">
                원가 항목의 수량은 RFQ에 먼저 적힌 값({pickers.map((c) => `${c.label} ${c.alternatives[0]?.value}`).join(", ")}) 기준으로
                넣으세요. 다른 값은 계산이 비례해 바꿉니다.
              </p>
              {pickers.map((c) => {
                const issueId = c.issue_id ?? "";
                return (
                  <div key={issueId} className="flex flex-col gap-1.5" role="radiogroup" aria-label={`${c.label} 값 고르기`}>
                    <span className="text-dense font-medium text-ink">
                      {c.label}
                      {c.unit ? ` (${c.unit})` : ""}
                      <Link href={`/rfq/${encodeURIComponent(rfqId)}/consistency`} className="ml-2 text-label text-accent hover:underline">
                        이슈 보기
                      </Link>
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

          {byLine ? (
            <LinesEditor
              lines={lines}
              setLines={setLines}
              prices={prices}
              currency={form.currency}
              per={per}
              processValues={processValues}
              split={effectiveAward === "split"}
              suggested={result?.data.summary?.suggested ?? null}
            />
          ) : (
          <div className="flex flex-col gap-1.5">
            <span className="text-label font-semibold text-ink-muted">원가 항목 ({per}당 수량 × 단가)</span>
            {hints.length > 0 && (
              <p className="text-label text-ink-muted" data-testid="item-hints">
                RFQ 참고: {hints.join(" · ")}
              </p>
            )}
            {prices.length === 0 && (
              <p className="rounded-control bg-warn-tint px-3 py-2 text-label text-warn">
                단가표가 비어 있습니다. 첫 화면의 기본 설정(톱니바퀴)에서 단가를 넣으면 여기서 고를 수 있습니다.
              </p>
            )}
            <ItemsEditor rows={items} prices={prices} currency={form.currency} per={per} patch={patchItem} setRows={setItems} />
          </div>
          )}

          <div className="grid gap-3 sm:grid-cols-3">
            {!byLine && (
            <Field label="금형비 (총액)" hint="없으면 0">
              <input className={cx(input, "tabular")} value={form.tooling_amount} inputMode="decimal" data-testid="input-tooling"
                onChange={(e) => setForm({ tooling_amount: e.target.value.trim() })} />
            </Field>
            )}
            {!byLine && (
            <Field label="금형비 회수">
              <select className={input} value={form.tooling_payment}
                onChange={(e) => setForm({ tooling_payment: e.target.value as Form["tooling_payment"] })}>
                <option value="amortized">단가에 분할</option>
                <option value="lump_sum">양산 시 일시 지급</option>
                <option value="deferred">후불(N년 뒤)</option>
              </select>
            </Field>
            )}
            {!byLine && form.tooling_payment === "deferred" && (
              <Field label="후불 연수">
                <input className={cx(input, "tabular")} value={form.deferred_years} inputMode="numeric"
                  onChange={(e) => setForm({ deferred_years: e.target.value.trim() })} />
              </Field>
            )}
            <Field label="설비 가동률 (%)" hint="최대 연 물량 기준. 비우면 중립">
              <input className={cx(input, "tabular")} value={form.capacity} inputMode="decimal" data-testid="input-capacity"
                onChange={(e) => setForm({ capacity: e.target.value.trim() })} />
            </Field>
            <Field label="전략적 가치 (0~100)" hint="신규 고객·차종 등. 비우면 중립">
              <input className={cx(input, "tabular")} value={form.strategic} inputMode="numeric" data-testid="input-strategic"
                onChange={(e) => setForm({ strategic: e.target.value.trim() })} />
            </Field>
          </div>
        </Step>

        <div className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
          <Button variant="primary" onClick={submit} disabled={problem !== null || isBusy} data-testid="calc">
            <IconCalc size={14} /> {result ? "다시 계산" : "계산하고 판정"}
          </Button>
          {problem && <span className="text-label text-warn" data-testid="calc-problem">{problem}</span>}
        </div>
        {error && (error.forbidden ? <ForbiddenState message={error.message} /> : <ErrorState message={error.message} />)}
      </Panel>

      <Panel title="결과" labelId="result-title" provenance={result?.provenance} className="xl:sticky xl:top-4 xl:self-start">
        {!result && <EmptyState title="아직 계산하지 않았습니다" />}
        {result && <Result run={result} />}
      </Panel>
    </div>
  );
}

function KnockoutCount({ asked, settled }: { asked: number; settled: number }) {
  return (
    <span className="text-label text-ink-muted tabular">
      질문 {asked} · 자동 확인 {settled}
    </span>
  );
}

function Cite({ evidence }: { evidence: VerifiedFinding | null }) {
  if (!evidence) return null;
  const verified = evidence.citation_status === "verified";
  return (
    <>
      {evidence.finding.citations.map((l, i) => (
        <CitationChip key={locusKey(l, i)} locus={l} verified={verified} />
      ))}
    </>
  );
}

const ANSWERS: { value: Answer; label: string }[] = [
  { value: "yes", label: "예" },
  { value: "no", label: "아니오" },
  { value: "unknown", label: "모름" },
];

function Question({
  r,
  department,
  value,
  onChange,
}: {
  r: RequirementCheck;
  department: string;
  value: Answer | undefined;
  onChange: (a: Answer) => void;
}) {
  return (
    <li className="flex flex-col gap-1.5 rounded-control border border-line p-3" data-testid={`question-${r.key}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-dense font-medium text-ink">{r.question}</span>
        <span className="rounded-chip border border-line px-1.5 text-label text-ink-muted">
          {r.kind === "fact" ? "사실 · 저장됨" : "판단 · 매번"}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div role="radiogroup" aria-label={r.question} className="flex gap-1">
          {ANSWERS.map((a) => (
            <button
              key={a.value}
              type="button"
              role="radio"
              aria-checked={value === a.value}
              data-testid={`answer-${r.key}-${a.value}`}
              onClick={() => onChange(a.value)}
              className={cx(
                "min-h-8 rounded-control border px-3 text-dense font-medium transition-soft",
                value === a.value
                  ? a.value === "no"
                    ? "border-danger bg-danger-tint text-danger"
                    : a.value === "yes"
                      ? "border-ok bg-ok-tint text-ok"
                      : "border-ink bg-ink text-white"
                  : "border-line-control text-ink hover:bg-canvas",
              )}
            >
              {a.label}
            </button>
          ))}
        </div>
        <span className="text-label text-ink-muted">참고 부서: {department}</span>
        <Cite evidence={r.evidence} />
      </div>
    </li>
  );
}

function VolumesEditor({ rows, setRows, per }: { rows: VolumeRow[]; setRows: (rows: VolumeRow[]) => void; per: string }) {
  return (
    <div className="flex flex-col gap-1.5" data-testid="volumes">
      <span className="text-label font-semibold text-ink-muted">연도별 물량 ({per})</span>
      <div className="flex flex-wrap gap-2">
        {rows.map((v, i) => (
          <div key={v.key} className="flex items-center gap-1 rounded-control border border-line p-1">
            <input aria-label={`${i + 1}번째 연도`} value={v.year} inputMode="numeric"
              className="min-h-8 w-16 rounded-control border border-line-control bg-surface px-1.5 text-dense text-ink tabular"
              onChange={(e) => setRows(rows.map((x) => (x.key === v.key ? { ...x, year: e.target.value.trim() } : x)))} />
            <input aria-label={`${v.year}년 물량`} value={v.quantity} inputMode="numeric"
              className="min-h-8 w-24 rounded-control border border-line-control bg-surface px-1.5 text-dense text-ink tabular"
              onChange={(e) => setRows(rows.map((x) => (x.key === v.key ? { ...x, quantity: e.target.value.trim() } : x)))} />
            <button type="button" aria-label={`${v.year}년 빼기`} className="px-1 text-label text-ink-muted hover:text-ink"
              onClick={() => setRows(rows.filter((x) => x.key !== v.key))}>
              ×
            </button>
          </div>
        ))}
        <Button
          variant="ghost"
          onClick={() => {
            const last = rows.at(-1);
            setRows([...rows, { key: newKey(), year: last ? String(Number(last.year) + 1) : "", quantity: last?.quantity ?? "" }]);
          }}
        >
          연도 추가
        </Button>
      </div>
    </div>
  );
}

function ItemsEditor({
  rows,
  prices,
  currency,
  per,
  patch,
  setRows,
}: {
  rows: ItemRow[];
  prices: PriceRow[];
  currency: string;
  per: string;
  patch: (key: string, p: Partial<ItemRow>) => void;
  setRows: (fn: (rows: ItemRow[]) => ItemRow[]) => void;
}) {
  const cell = "min-h-8 w-full rounded-control border border-line-control bg-surface px-1.5 text-dense text-ink";
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-left text-dense" data-testid="cost-items">
        <thead className="text-label text-ink-muted">
          <tr>
            <th scope="col" className="py-1 pr-2">단가표에서</th>
            <th scope="col" className="px-1 py-1">분류</th>
            <th scope="col" className="px-1 py-1">이름</th>
            <th scope="col" className="px-1 py-1">{per}당 수량</th>
            <th scope="col" className="px-1 py-1">단위</th>
            <th scope="col" className="px-1 py-1">단가 ({currency})</th>
            <th scope="col" className="py-1"><span className="sr-only">동작</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const priced = r.price_id ? prices.find((p) => p.id === r.price_id) : undefined;
            return (
              <tr key={r.key} className="border-t border-line" data-testid="cost-item">
                <td className="w-48 py-1 pr-2">
                  <select
                    aria-label={`${i + 1}행 단가표 선택`}
                    className={cell}
                    value={r.price_id ?? ""}
                    onChange={(e) => {
                      const row = prices.find((p) => p.id === e.target.value);
                      patch(
                        r.key,
                        row
                          ? { price_id: row.id, label: row.name, category: row.category, unit: row.unit, unit_price: row.price }
                          : { price_id: null },
                      );
                    }}
                  >
                    <option value="">직접 입력</option>
                    {ITEM_CATEGORIES.map((c) => {
                      const group = prices.filter((p) => p.category === c);
                      if (group.length === 0) return null;
                      return (
                        <optgroup key={c} label={CATEGORY_LABEL[c]}>
                          {group.map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.name} · {p.price} {p.currency}/{p.unit}
                            </option>
                          ))}
                        </optgroup>
                      );
                    })}
                  </select>
                </td>
                <td className="w-28 px-1 py-1">
                  <select aria-label={`${i + 1}행 분류`} className={cell} value={r.category} disabled={!!priced}
                    onChange={(e) => patch(r.key, { category: e.target.value as ItemCategory })}>
                    {ITEM_CATEGORIES.map((c) => (
                      <option key={c} value={c}>
                        {CATEGORY_LABEL[c]}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="px-1 py-1">
                  <input aria-label={`${i + 1}행 이름`} className={cell} value={r.label} maxLength={80} readOnly={!!priced}
                    placeholder="예: 프레스 400톤" onChange={(e) => patch(r.key, { label: e.target.value })} />
                </td>
                <td className="w-24 px-1 py-1">
                  <input aria-label={`${i + 1}행 수량`} className={cx(cell, "tabular")} value={r.quantity} inputMode="decimal"
                    onChange={(e) => patch(r.key, { quantity: e.target.value.trim() })} />
                </td>
                <td className="w-16 px-1 py-1">
                  <select aria-label={`${i + 1}행 단위`} className={cell} value={r.unit} disabled={!!priced}
                    onChange={(e) => patch(r.key, { unit: e.target.value })}>
                    {unitOptions(r.unit).map((u) => (
                      <option key={u.code} value={u.code}>
                        {u.label}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="w-28 px-1 py-1">
                  <input aria-label={`${i + 1}행 단가`} className={cx(cell, "tabular", !r.unit_price && "border-warn")}
                    value={r.unit_price} inputMode="decimal" readOnly={!!priced} placeholder="모름"
                    onChange={(e) => patch(r.key, { unit_price: e.target.value.trim() })} />
                </td>
                <td className="whitespace-nowrap py-1 pl-1">
                  <Button variant="ghost" aria-label={`${i + 1}행 삭제`} onClick={() => setRows((list) => list.filter((x) => x.key !== r.key))}>
                    ×
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <Button
        variant="ghost"
        onClick={() =>
          setRows((list) => [
            ...list,
            { key: newKey(), label: "", category: "process", quantity: "", unit: "h", unit_price: "", price_id: null },
          ])
        }
        data-testid="item-add"
      >
        항목 추가
      </Button>
    </div>
  );
}

/** Which price-sheet groups to price: the lines are filled from the RFQ, the user only adds prices. */
function SheetPicker({ sheet, total, limit, apply }: { sheet: RfqLine[]; total: number; limit: number; apply: (rows: RfqLine[]) => void }) {
  const groups = [...new Set(sheet.map((r) => r.group ?? "기타"))];
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(groups));
  const picked = sheet.filter((r) => chosen.has(r.group ?? "기타"));
  const pages = [...new Set(sheet.map((r) => r.page))];
  const toggle = (g: string, on: boolean) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (on) next.add(g);
      else next.delete(g);
      return next;
    });
  return (
    <div className="flex flex-col gap-2 rounded-control border border-line p-3" data-testid="sheet-picker">
      <p className="text-label text-ink-muted">
        <span className="font-semibold text-ink">RFQ 가격표에서 품목 {sheet.length}개를 찾았습니다</span> ({pages[0]}
        {pages.length > 1 ? `–${pages[pages.length - 1]}` : ""}쪽). 품목 이름과 수량은 RFQ에 적힌 그대로이고, 매입가만 넣으면 됩니다. 빠진 품목은 아래에서 직접 추가하세요(다시 채워도 유지됩니다).
        {total > limit && ` 품목이 많아 앞의 ${limit}개만 불러왔습니다(전체 ${total}개).`}
      </p>
      {groups.length > 1 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1.5" aria-label="가격표 분류">
          {groups.map((g) => (
            <label key={g} className="flex items-center gap-1.5 text-dense text-ink">
              <input type="checkbox" checked={chosen.has(g)} onChange={(e) => toggle(g, e.target.checked)} />
              {g} ({sheet.filter((r) => (r.group ?? "기타") === g).length})
            </label>
          ))}
        </div>
      )}
      <div>
        <Button variant="ghost" data-testid="sheet-apply" disabled={picked.length === 0} onClick={() => apply(picked)}>
          선택한 {picked.length}개로 품목 다시 채우기
        </Button>
      </div>
    </div>
  );
}

function LinesEditor({
  lines,
  setLines,
  prices,
  currency,
  per,
  processValues,
  split,
  suggested,
}: {
  lines: LineRow[];
  setLines: (fn: (rows: LineRow[]) => LineRow[]) => void;
  prices: PriceRow[];
  currency: string;
  per: string;
  processValues: string[];
  /** Lines can be left out only when the award is split. */
  split: boolean;
  /** Indexes of the lines the last calculation found biddable, when it priced lines. */
  suggested: number[] | null;
}) {
  const patch = (key: string, p: Partial<LineRow>) => setLines((list) => list.map((l) => (l.key === key ? { ...l, ...p } : l)));
  // Many lines (a price sheet) are shown one row each; a line opens to its full editor.
  const compact = lines.length > 5;
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const toggleOpen = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const unpriced = lines.filter((l) => l.items.some((r) => !r.unit_price.trim())).length;
  return (
    <div className="flex flex-col gap-3" data-testid="lines">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-label font-semibold text-ink-muted">
          품목 {lines.length}개{unpriced > 0 && ` · 단가를 넣을 품목 ${unpriced}개`}
        </span>
        {split && suggested && suggested.length > 0 && (
          <Button
            variant="ghost"
            data-testid="lines-suggested"
            onClick={() => setLines((list) => list.map((l, i) => ({ ...l, included: suggested.includes(i) })))}
          >
            추천 품목만 입찰
          </Button>
        )}
      </div>
      {compact && (
        <div className="overflow-x-auto rounded-control border border-line">
          <table className="w-full text-dense" data-testid="lines-table">
            <thead className="border-b border-line bg-surface-source text-label text-ink-muted">
              <tr>
                <th className="px-2 py-1.5 text-left font-semibold">#</th>
                <th className="px-2 py-1.5 text-left font-semibold">품목</th>
                <th className="px-2 py-1.5 text-right font-semibold">물량</th>
                <th className="px-2 py-1.5 text-left font-semibold">매입가 ({currency}/{per})</th>
                <th className="px-2 py-1.5 text-left font-semibold">목표가</th>
                <th className="px-2 py-1.5 text-left font-semibold">입찰</th>
                <th className="px-2 py-1.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {lines.map((line, i) => {
                const first = line.items[0];
                const quantity = line.volumes.reduce((n, v) => n + (Number(v.quantity) || 0), 0);
                return (
                  <tr key={line.key} className={cx(split && !line.included && "opacity-60")}>
                    <td className="px-2 py-1 whitespace-nowrap text-ink-muted tabular">{i + 1}</td>
                    <td className="px-2 py-1 text-ink">
                      <input className={cx(input, "w-full min-w-48")} value={line.item} maxLength={200} aria-label={`품목 ${i + 1} 이름`}
                        onChange={(e) => patch(line.key, { item: e.target.value })} />
                      {line.group && <span className="text-label text-ink-muted">{line.group}{line.page ? ` · ${line.page}쪽` : ""}</span>}
                    </td>
                    <td className="px-2 py-1 text-right tabular">
                      {line.volumes.length === 1 && line.volumes[0] ? (
                        <input className={cx(input, "w-24 text-right tabular")} value={line.volumes[0].quantity} inputMode="numeric"
                          aria-label={`품목 ${i + 1} 물량`}
                          onChange={(e) => patch(line.key, { volumes: [{ ...line.volumes[0]!, quantity: e.target.value.trim() }] })} />
                      ) : (
                        quantity.toLocaleString()
                      )}
                    </td>
                    <td className="px-2 py-1">
                      {line.items.length === 1 && first ? (
                        <input className={cx(input, "w-28 tabular")} value={first.unit_price} inputMode="decimal"
                          aria-label={`품목 ${i + 1} ${first.label} 단가`}
                          onChange={(e) => patch(line.key, { items: [{ ...first, unit_price: e.target.value.trim(), price_id: null }] })} />
                      ) : (
                        <span className="text-label text-ink-muted">원가 항목 {line.items.length}개</span>
                      )}
                    </td>
                    <td className="px-2 py-1">
                      <input className={cx(input, "w-24 tabular")} value={line.target} inputMode="decimal" aria-label={`품목 ${i + 1} 목표가`}
                        onChange={(e) => patch(line.key, { target: e.target.value.trim() })} />
                    </td>
                    <td className="px-2 py-1">
                      <input type="checkbox" checked={split ? line.included : true} disabled={!split} aria-label={`품목 ${i + 1} 입찰 포함`}
                        onChange={(e) => patch(line.key, { included: e.target.checked })} />
                    </td>
                    <td className="px-2 py-1 text-right whitespace-nowrap">
                      <button type="button" className="text-label text-accent hover:underline" onClick={() => toggleOpen(line.key)}>
                        {open.has(line.key) ? "닫기" : "자세히"}
                      </button>
                      <button type="button" className="ml-2 text-label text-danger hover:underline" aria-label={`품목 ${i + 1} 삭제`}
                        onClick={() => setLines((list) => list.filter((l) => l.key !== line.key))}>
                        삭제
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {lines.map((line, i) => (compact && !open.has(line.key)) ? null : (
        <fieldset key={line.key} className={cx("flex flex-col gap-3 rounded-control border border-line p-3", split && !line.included && "opacity-60")}
          data-testid="line">
          <legend className="px-1 text-label font-semibold text-ink-muted">품목 {i + 1}</legend>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="품목 이름">
              <input className={cx(input, "w-64")} value={line.item} maxLength={200} aria-label={`품목 ${i + 1} 이름`}
                onChange={(e) => patch(line.key, { item: e.target.value })} />
            </Field>
            <Field label={`목표가 (${per}당)`} hint="없으면 비워 두세요">
              <input className={cx(input, "w-32 tabular")} value={line.target} inputMode="decimal" aria-label={`품목 ${i + 1} 목표가`}
                onChange={(e) => patch(line.key, { target: e.target.value.trim() })} />
            </Field>
            <label className="flex min-h-9 items-center gap-1.5 text-dense text-ink">
              <input type="checkbox" checked={split ? line.included : true} disabled={!split} aria-label={`품목 ${i + 1} 입찰 포함`}
                onChange={(e) => patch(line.key, { included: e.target.checked })} />
              입찰 포함{!split && " (일괄 낙찰)"}
            </label>
            {lines.length > 1 && (
              <Button variant="ghost" className="ml-auto" aria-label={`품목 ${i + 1} 삭제`}
                onClick={() => setLines((list) => list.filter((l) => l.key !== line.key))}>
                품목 삭제
              </Button>
            )}
          </div>
          <VolumesEditor rows={line.volumes} setRows={(rows) => patch(line.key, { volumes: rows })} per={per} />
          {processValues.length > 0 && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
              <span className="text-label font-semibold text-ink-muted">필요한 공정</span>
              {processValues.map((v) => (
                <label key={v} className="flex items-center gap-1.5 text-dense text-ink">
                  <input
                    type="checkbox"
                    aria-label={`품목 ${i + 1} 공정 ${v}`}
                    checked={line.processes.includes(v)}
                    onChange={(e) =>
                      patch(line.key, { processes: e.target.checked ? [...line.processes, v] : line.processes.filter((x) => x !== v) })
                    }
                  />
                  {v}
                </label>
              ))}
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <span className="text-label font-semibold text-ink-muted">원가 항목 ({per}당 수량 × 단가)</span>
            <ItemsEditor
              rows={line.items}
              prices={prices}
              currency={currency}
              per={per}
              patch={(key, p) => patch(line.key, { items: line.items.map((r) => (r.key === key ? { ...r, ...p } : r)) })}
              setRows={(fn) => setLines((list) => list.map((l) => (l.key === line.key ? { ...l, items: fn(l.items) } : l)))}
            />
          </div>
        </fieldset>
      ))}
      <AddLineForm
        currency={currency}
        per={per}
        year={lines[0]?.volumes[0]?.year ?? String(new Date().getFullYear() + 1)}
        add={(line) => setLines((list) => [...list, { ...line, processes: processValues }])}
      />
      <Button
        variant="ghost"
        data-testid="line-add"
        onClick={() =>
          setLines((list) => [
            ...list,
            {
              key: newKey(),
              item: `품목 ${list.length + 1}`,
              target: "",
              volumes: (list[0]?.volumes ?? []).map((v) => ({ ...v, key: newKey() })),
              items: [{ key: newKey(), label: "", category: "material", quantity: "", unit: "kg", unit_price: "", price_id: null }],
              processes: processValues,
              included: true,
            },
          ])
        }
      >
        빈 품목 추가 (원가 항목 직접 구성)
      </Button>
    </div>
  );
}

const MANUAL = "직접 추가";

/** A line the price-sheet reading missed, added by hand: name, part number, quantity and prices. */
function AddLineForm({ currency, per, year, add }: { currency: string; per: string; year: string; add: (line: LineRow) => void }) {
  const empty = { name: "", part: "", quantity: "", price: "", target: "" };
  const [draft, setDraft] = useState(empty);
  const [isOpen, setIsOpen] = useState(false);
  const set = (p: Partial<typeof empty>) => setDraft((d) => ({ ...d, ...p }));
  const problem = !draft.name.trim()
    ? "품목 이름을 넣으세요."
    : !isInt(draft.quantity) || Number(draft.quantity) <= 0
      ? "물량을 1 이상의 정수로 넣으세요."
      : draft.price && !isDecimal(draft.price)
        ? "매입가는 숫자로 넣으세요."
        : draft.target && !isDecimal(draft.target)
          ? "목표가는 숫자로 넣으세요."
          : null;
  function submit() {
    if (problem) return;
    const part = draft.part.trim();
    add({
      key: newKey(),
      item: part ? `${draft.name.trim()} (${part})` : draft.name.trim(),
      target: draft.target,
      volumes: [{ key: newKey(), year, quantity: draft.quantity }],
      items: [{ key: newKey(), label: "매입가", category: "purchased", quantity: "1", unit: "개", unit_price: draft.price, price_id: null }],
      processes: [],
      included: true,
      group: MANUAL,
    });
    setDraft(empty);
    setIsOpen(false);
  }
  if (!isOpen) {
    return (
      <div>
        <Button variant="ghost" data-testid="add-open" onClick={() => setIsOpen(true)}>
          + 빠진 품목 직접 추가
        </Button>
      </div>
    );
  }
  return (
    <fieldset className="flex flex-col gap-2 rounded-control border border-dashed border-line-control p-3" data-testid="line-add-form">
      <legend className="px-1 text-label font-semibold text-ink-muted">빠진 품목 직접 추가</legend>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="품목 이름">
          <input className={cx(input, "w-64")} value={draft.name} maxLength={160} data-testid="add-name"
            onChange={(e) => set({ name: e.target.value })} />
        </Field>
        <Field label="부품 번호" hint="선택">
          <input className={cx(input, "w-40")} value={draft.part} maxLength={38} data-testid="add-part"
            onChange={(e) => set({ part: e.target.value })} />
        </Field>
        <Field label={`물량 (${year}년)`}>
          <input className={cx(input, "w-28 tabular")} value={draft.quantity} inputMode="numeric" data-testid="add-quantity"
            onChange={(e) => set({ quantity: e.target.value.trim() })} />
        </Field>
        <Field label={`매입가 (${currency}/${per})`} hint="나중에 넣어도 됩니다">
          <input className={cx(input, "w-28 tabular")} value={draft.price} inputMode="decimal" data-testid="add-price"
            onChange={(e) => set({ price: e.target.value.trim() })} />
        </Field>
        <Field label="목표가" hint="선택">
          <input className={cx(input, "w-24 tabular")} value={draft.target} inputMode="decimal" data-testid="add-target"
            onChange={(e) => set({ target: e.target.value.trim() })} />
        </Field>
        <Button data-testid="add-submit" disabled={problem !== null} onClick={submit}>
          목록에 추가
        </Button>
        <Button variant="ghost" data-testid="add-cancel" onClick={() => { setDraft(empty); setIsOpen(false); }}>
          취소
        </Button>
      </div>
      {problem && (draft.name || draft.quantity || draft.price || draft.target) && <p className="text-label text-ink-muted">{problem}</p>}
    </fieldset>
  );
}

// ── result ──────────────────────────────────────────────────────────────────
const DECISION: Record<DecisionStatus, { label: string; className: string; icon: ReactNode }> = {
  go: { label: "Go", className: "border-ok bg-ok-tint text-ok", icon: <IconCheck size={16} /> },
  conditional: { label: "조건부 Go", className: "border-warn bg-warn-tint text-warn", icon: <IconWarn size={16} /> },
  no_go: { label: "No-Go", className: "border-danger bg-danger-tint text-danger", icon: <IconWarn size={16} /> },
  hold: { label: "보류", className: "border-line-control bg-surface text-ink", icon: <IconPause size={16} /> },
  baseline_undefined: { label: "기준 없음", className: "border-line-control bg-surface text-ink-muted", icon: null },
  not_applicable: { label: "자동 판정 대상 아님", className: "border-line-control bg-surface text-ink-muted", icon: null },
};

/** "1234567.8900" → "1,234,567.89": thousands grouped, trailing zeros after the point dropped. */
function grouped(value: string): string {
  const [whole, fraction = ""] = value.split(".");
  const sign = whole.startsWith("-") ? "-" : "";
  const digits = whole.replace("-", "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const kept = fraction.replace(/0+$/, "");
  return `${sign}${digits}${kept ? `.${kept}` : ""}`;
}

type Cost = NonNullable<PrecostResponse["data"]["cost"]>;

function QuoteTiles({ c, cur }: { c: Cost; cur: string }) {
  const resale = c.cost_basis === "resale";
  const tiles = [
    { key: "min", title: "최소 견적 (수명주기 평균 마진 유지)", unit: c.min_quote, total: c.totals.min_quote, markup: c.markup?.min_quote, tone: "bg-canvas", titleTone: "text-ink-muted" },
    { key: "safe", title: "안전 견적 (복합 충격 반영)", unit: c.safe_quote, total: c.totals.safe_quote, markup: c.markup?.safe_quote, tone: "bg-accent-tint", titleTone: "text-accent" },
  ];
  return (
    <div className="flex flex-col gap-1.5">
      <div className="grid grid-cols-2 gap-3">
        {tiles.map((t) => (
          <div key={t.key} className={cx("flex flex-col gap-1 rounded-control p-3", t.tone)} data-testid={`${t.key}-tile`}>
            <span className={cx("text-label", t.titleTone)}>{t.title}</span>
            <span className="text-heading font-semibold text-ink tabular" data-testid={`${t.key}-quote`}>
              {grouped(t.unit)} <span className="text-label">{cur} / {resale ? "단위" : "개"}</span>
            </span>
            {t.markup !== undefined && (
              <span className="text-label text-ink tabular" data-testid={`${t.key}-markup`}>
                매입가 대비 마크업 {fractionToPercent(t.markup)} %
              </span>
            )}
            <span className="border-t border-line pt-1 text-dense text-ink tabular" data-testid={`${t.key}-total`}>
              총 견적 <span className="font-semibold">{grouped(t.total)}</span> {cur}
            </span>
          </div>
        ))}
      </div>
      <p className="text-label text-ink-muted tabular">
        총 견적 = 연도별 물량 × 그 해 단가(연간 인하 반영)의 합, 전체 {c.total_quantity.toLocaleString("ko-KR")}{" "}
        {resale ? "단위" : "개"}.
        {resale && c.markup
          ? ` 단위는 물량을 넣은 기준과 같습니다(예: 매입 100 ${cur}어치 = 1단위). 단위당 매입가 ${grouped(c.markup.purchased)} ${cur}.`
          : ""}
      </p>
    </div>
  );
}

function DecisionBox({ d }: { d: PrecostResponse["data"] }) {
  const decision = DECISION[d.decision.status];
  return (
      <section
        aria-labelledby="decision-title"
        className={cx("flex flex-col gap-2 rounded-control border p-3", decision.className)}
        data-testid="decision"
        data-status={d.decision.status}
      >
        <h3 id="decision-title" className="sr-only">판정</h3>
        <div className="flex flex-wrap items-center gap-3">
          <span className="inline-flex items-center gap-1.5 text-title font-semibold" data-testid="decision-status">
            {decision.icon}
            {decision.label}
          </span>
          {d.decision.score !== null && (
            <span className="ml-auto text-title font-semibold tabular" data-testid="decision-score">
              {d.decision.score}
              <span className="text-label font-medium"> / 100점</span>
            </span>
          )}
        </div>
        <ul className="flex list-disc flex-col gap-0.5 pl-5 text-dense text-ink">
          {d.decision.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
        <p className="text-label text-ink-muted" data-testid="decision-rule">
          판정 규칙 <code className="font-semibold text-ink">{d.decision.rule}</code> · data/rules/cost_model.yaml을 그대로 적용한
          결과이며, 입찰 여부는 원가 담당이 정합니다.
        </p>
      </section>
  );
}

function ScoreTable({ score }: { score: NonNullable<PrecostResponse["data"]["score"]> }) {
  return (
    <table className="w-full text-left text-dense tabular" data-testid="score-items">
      <caption className="pb-1 text-left text-label font-semibold text-ink-muted">
        가중 점수 {score.total}점 (Go ≥ 70 · 조건부 50~70)
      </caption>
      <tbody>
        {score.items.map((s) => (
          <tr key={s.id} className="border-t border-line">
            <td className="py-1.5 pr-2 text-ink">{s.label}</td>
            <td className="py-1.5 pr-2 text-right text-label text-ink-muted">×{s.weight}%</td>
            <td className="py-1.5 pr-2 text-right font-semibold text-ink">{s.score}</td>
            <td className="py-1.5 text-label text-ink-muted">{s.note}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function KnockoutStatus({ k }: { k: PrecostResponse["data"]["knockout"] }) {
  if (k.checks.length === 0) return null;
  return (
    <p className="text-label text-ink-muted" data-testid="knockout-status" data-status={k.status}>
      1차 넉아웃:{" "}
      <strong className="text-ink">{k.status === "passed" ? "모두 충족" : k.status === "failed" ? "불충족 있음" : "판단 불가 있음"}</strong> (
      {k.checks.length}개 조건)
    </p>
  );
}

const AWARD_SOURCE = { input: "직접 고름", rfq: "RFQ에서 읽음", assumed: "RFQ에 없어 가정" } as const;

function LinesResult({ d }: { d: PrecostResponse["data"] }) {
  const cur = d.currency;
  const lines = d.lines ?? [];
  const summary = d.summary;
  if (!summary) return null;
  const split = d.award?.mode === "split";
  return (
    <div className="flex flex-col gap-4 p-4" data-testid="precost-result">
      <DecisionBox d={d} />
      {d.award && (
        <p className="text-label text-ink-muted" data-testid="result-award">
          낙찰 방식: <strong className="text-ink">{d.award.label}</strong> ({AWARD_SOURCE[d.award.source]}) · 입찰 품목 {summary.lines_bid}/
          {lines.length}개
        </p>
      )}
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1 rounded-control bg-canvas p-3" data-testid="lines-min-total">
          <span className="text-label text-ink-muted">최소 견적 총액 (입찰 품목 합계)</span>
          <span className="text-heading font-semibold text-ink tabular">
            {grouped(summary.totals.min_quote)} <span className="text-label">{cur}</span>
          </span>
        </div>
        <div className="flex flex-col gap-1 rounded-control bg-accent-tint p-3" data-testid="lines-safe-total">
          <span className="text-label text-accent">안전 견적 총액</span>
          <span className="text-heading font-semibold text-ink tabular">
            {grouped(summary.totals.safe_quote)} <span className="text-label">{cur}</span>
          </span>
        </div>
      </div>
      <p className="-mt-2 text-label text-ink-muted tabular">
        전체 {summary.total_quantity.toLocaleString("ko-KR")}개
        {summary.target_total !== null ? ` · 목표가 총액 ${grouped(summary.target_total)} ${cur}` : " · 목표가가 없는 품목이 있어 목표가 총액 없음"}
        {Number(summary.shared_costs) > 0 ? ` · 공통비 ${grouped(summary.shared_costs)} ${cur} → 개당 ${summary.shared_per_unit} 배분` : ""}
      </p>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-dense tabular" data-testid="line-results">
          <caption className="pb-1 text-left text-label font-semibold text-ink-muted">품목별 결과 (단가는 개당)</caption>
          <thead className="text-label text-ink-muted">
            <tr>
              <th scope="col" className="py-1 pr-2">품목</th>
              <th scope="col" className="px-1 py-1 text-right">목표가</th>
              <th scope="col" className="px-1 py-1 text-right">최소</th>
              <th scope="col" className="px-1 py-1 text-right">안전</th>
              <th scope="col" className="px-1 py-1 text-right">총 견적 (최소)</th>
              <th scope="col" className="py-1 pl-2">판정</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => {
              const status = DECISION[l.decision.status];
              return (
                <tr key={`${l.item}-${i}`} className={cx("border-t border-line", !l.included && "text-ink-muted")} data-testid="line-result"
                  data-status={l.decision.status} data-included={l.included}>
                  <td className="py-1.5 pr-2">
                    <span className={cx("font-medium", l.included ? "text-ink" : "line-through")}>{l.item}</span>
                    {split && summary.suggested.includes(i) && <span className="ml-1.5 text-label text-ok">추천</span>}
                    {!l.included && <span className="ml-1.5 text-label">입찰 제외</span>}
                  </td>
                  <td className="px-1 py-1.5 text-right">{l.target_price ?? "–"}</td>
                  <td className="px-1 py-1.5 text-right">{l.min_quote}</td>
                  <td className="px-1 py-1.5 text-right">{l.safe_quote}</td>
                  <td className="px-1 py-1.5 text-right">{grouped(l.totals.min_quote)}</td>
                  <td className="py-1.5 pl-2">
                    <span className={cx("inline-flex rounded-control border px-1.5 text-label font-semibold", status.className)}>
                      {status.label}
                      {l.decision.score !== null ? ` ${l.decision.score}` : ""}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <details className="text-dense">
        <summary className="cursor-pointer text-label font-semibold text-ink-muted">품목별 판정 근거와 원가 구성</summary>
        <ul className="mt-2 flex flex-col gap-3">
          {lines.map((l, i) => (
            <li key={`${l.item}-${i}`} className="flex flex-col gap-1">
              <span className="font-semibold text-ink">{l.item}</span>
              <ul className="list-disc pl-5 text-label text-ink">
                {l.decision.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
              <p className="text-label text-ink-muted tabular">
                {l.lines.map((c) => `${c.label} ${c.value}`).join(" · ")} → 개당 원가 {l.piece_cost} {cur}
              </p>
            </li>
          ))}
        </ul>
      </details>
      {d.score && <ScoreTable score={d.score} />}
      <KnockoutStatus k={d.knockout} />
    </div>
  );
}

function Result({ run }: { run: PrecostResponse }) {
  const d = run.data;
  if (d.lines) return <LinesResult d={d} />;
  if (!d.cost) return null;
  const cost = d.cost;
  const cur = d.currency;
  const resale = cost.cost_basis === "resale";
  return (
    <div className="flex flex-col gap-4 p-4" data-testid="precost-result">
      <DecisionBox d={d} />

      <QuoteTiles c={cost} cur={cur} />
      <p className="-mt-2 text-label text-ink-muted tabular">
        {resale ? "단위당 원가" : "개당 원가"} {cost.piece_cost}
        {cost.tooling_per_piece && Number(cost.tooling_per_piece) > 0 ? ` + 금형 ${cost.tooling_per_piece}` : ""} · 수명주기 계수 {cost.lifetime_factor}
        {cost.material_indexed ? " · 재료비 연동(인하 제외)" : ""}
        {resale ? " · 유통 산식: 불량·판관비 가산 없음, 운영비는 원가 항목으로 직접 반영" : ""}
      </p>

      {cost.lines.length > 0 && (
        <table className="w-full text-left text-dense tabular" data-testid="cost-lines">
          <caption className="pb-1 text-left text-label font-semibold text-ink-muted">
            원가 구성 ({resale ? "단위당" : "개당"}, 가장 불리한 경우)
          </caption>
          <tbody>
            {cost.lines.map((l) => (
              <tr key={l.category} className="border-t border-line" data-category={l.category}>
                <td className="py-1.5 pr-2 text-ink">{l.label}</td>
                <td className="py-1.5 text-right font-semibold text-ink">
                  {l.value} {cur}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {d.score && <ScoreTable score={d.score} />}
      <KnockoutStatus k={d.knockout} />

      <details className="text-dense" open={cost.scenarios.length > 1}>
        <summary className="cursor-pointer text-label font-semibold text-ink-muted">충격과 충돌 경우</summary>
        <table className="mt-1 w-full text-left tabular" data-testid="shocks">
          <tbody>
            {cost.shocks.map((s) => (
              <tr key={s.id} className="border-t border-line">
                <td className={cx("py-1.5 pr-2", s.applied ? "text-ink" : "text-ink-muted line-through")}>{s.label}</td>
                <td className="py-1.5 pr-2 text-right font-semibold text-ink">
                  {s.min_quote_delta !== null && s.applied ? `${s.min_quote_delta} ${cur}` : ""}
                </td>
                <td className="py-1.5 text-label text-ink-muted">{s.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {cost.scenarios.length > 1 && (
          <table className="mt-3 w-full text-left tabular" data-testid="scenarios">
            <caption className="pb-1 text-left text-label text-ink-muted">충돌 값별 최소 견적 (가장 불리한 값을 씀)</caption>
            <tbody>
              {cost.scenarios.map((s) => (
                <tr key={s.label} className="border-t border-line">
                  <td className="py-1.5 pr-2 text-ink">{s.label}</td>
                  <td className="py-1.5 text-right font-semibold text-ink">
                    {s.min_quote} {cur}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>

      <p className="text-label text-ink-muted tabular" data-testid="npv">
        참고 NPV ({cost.npv.price_basis === "target_price" ? "목표가" : "최소 견적"} 기준, 할인율{" "}
        {fractionToPercent(cost.npv.discount_rate)}%): <strong className="text-ink">{cost.npv.value} {cur}</strong>
        {cost.npv.payback_year !== null ? ` · 회수 ${cost.npv.payback_year}년` : " · 기간 내 회수 안 됨"} · 판정에는 쓰지
        않습니다.
      </p>
    </div>
  );
}
