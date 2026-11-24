/**
 * Form state of the pre-cost screen: what the user entered, its first values from the backend's setup,
 * and the request built from it. No number is computed here.
 */
import type { ReactNode } from "react";
import type {
  CompanyResponse,
  CostInputCandidate,
  ItemCategory,
  PrecostInputs,
  PrecostSetupResponse,
  RfqLine,
  VerifiedFinding,
} from "./contract";
import { fractionToPercent, isDecimal, isInt, percentToFraction } from "./precost";

export type Answer = "yes" | "no" | "unknown";
export type Setup = PrecostSetupResponse["data"];
export type Company = CompanyResponse["data"];

export type ItemRow = {
  key: string;
  label: string;
  category: ItemCategory;
  quantity: string;
  unit: string;
  /** Empty: the user does not know it yet. The calculation then stops instead of using 0. */
  unit_price: string;
  price_id: string | null;
};
export type VolumeRow = { key: string; year: string; quantity: string };
/** One line item priced on its own (line-item pricing). */
export type LineRow = {
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
export const AWARD_RFQ: Record<string, string> = {
  // The same words as the RFQ's award label above (cost_model.yaml, EITHER_LABEL in calc.py).
  split: "품목별 낙찰 가능",
  all_or_none: "일괄 낙찰만 가능",
  either: "품목별 낙찰 가능 (일괄 낙찰도 가능, 발주처가 선택)",
  none: "명시 없음 → 일괄로 가정",
};
export type AwardChoice = "rfq" | "split" | "all_or_none";
/** Line-item pricing as entered, or null when the RFQ is priced as one item. */
export type LineSpec = { lines: LineRow[]; award: AwardChoice; shared: string } | null;
export type Form = {
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
export const newKey = () => `k${++nextKey}`;
export const candidate = (setup: Setup, field: string) => setup.cost_inputs.candidates.find((c) => c.field === field);

export function initialForm(setup: Setup, company: Company): Form {
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

export function initialVolumes(setup: Setup): VolumeRow[] {
  if (setup.cost_inputs.volumes.length > 0) {
    return setup.cost_inputs.volumes.map((v) => ({ key: newKey(), year: String(v.year), quantity: String(v.quantity) }));
  }
  const total = candidate(setup, "volume")?.value ?? "";
  return [{ key: newKey(), year: String(new Date().getFullYear() + 1), quantity: total }];
}

export function initialItems(setup: Setup): ItemRow[] {
  const suggested = setup.suggested_items.map((s) => ({
    key: newKey(),
    label: s.label,
    category: s.category,
    quantity: s.quantity === "0" ? "" : s.quantity,
    unit: s.unit,
    unit_price: s.unit_price,
    price_id: s.price_id,
  }));
  if (suggested.length > 0) return suggested;
  // A distribution RFQ is priced per unit of purchases: one unit is 100 of the quote currency
  // bought, so its purchase cost is 100 by definition (not a value read from the RFQ).
  if (setup.classification.type === "distribution") {
    return [{ key: newKey(), label: RESALE_ITEM, category: "purchased", quantity: "1", unit: "묶음", unit_price: "100", price_id: null }];
  }
  return [{ key: newKey(), label: "", category: "material", quantity: "", unit: "kg", unit_price: "", price_id: null }];
}

export const RESALE_ITEM = "매입액 (1단위 = 100어치)";

/** Lines priced from the RFQ's price sheet: name and printed quantity; the user adds the purchase price. */
export function linesFromRfq(rows: RfqLine[], year: string, processes: string[]): LineRow[] {
  return rows.map((r) => ({
    key: newKey(),
    item: r.part ? `${r.name} (${r.part})` : r.name,
    target: "",
    // An order form leaves the quantity to the buyer: the user enters it (no number is made up).
    volumes: [{ key: newKey(), year, quantity: r.quantity === null ? "" : String(r.quantity) }],
    items: [{ key: newKey(), label: "매입가", category: "purchased", quantity: "1", unit: "개", unit_price: "", price_id: null }],
    processes,
    included: true,
    group: r.group,
    page: r.page,
  }));
}

export const SWITCH = { auto: null, yes: true, no: false } as const;
export const STARTS: Record<string, string> = { "0": "첫해부터", "1": "2년차부터", "2": "3년차부터" };

export type FieldSpec = {
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

export function volumesProblem(rows: VolumeRow[], where: string): string | null {
  return rows.length === 0 || rows.some((v) => !isInt(v.year) || !isInt(v.quantity)) ? `${where}연도별 물량을 정수로 넣으세요.` : null;
}

export function itemsProblem(rows: ItemRow[], where: string): string | null {
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

export const toVolumes = (rows: VolumeRow[]) => rows.map((v) => ({ year: Number(v.year), quantity: Number(v.quantity) }));
export const toItems = (rows: ItemRow[]) =>
  rows.map((r) => ({
    label: r.label.trim(),
    category: r.category,
    quantity: r.quantity.trim(),
    unit: r.unit.trim() || "개",
    unit_price: r.unit_price.trim(),
    price_id: r.price_id,
  }));

/** The request body, or the first problem that stops the calculation. */
export function build(
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
export function conflicts(candidates: CostInputCandidate[]): CostInputCandidate[] {
  const seen = new Set<string>();
  return candidates.filter((c) => {
    if (!c.issue_id || c.alternatives.length < 2 || seen.has(c.issue_id)) return false;
    seen.add(c.issue_id);
    return true;
  });
}
