/**
 * Helpers shared by the pre-cost screen and the company settings. Nothing here
 * calculates a cost: the backend does every number. These only check what the
 * user typed and build request bodies.
 */
import Decimal from "decimal.js";
import type { CompanyRequest, CompanyResponse, ItemCategory, PriceRow } from "./contract";

export const CATEGORY_LABEL: Record<ItemCategory, string> = {
  material: "재료",
  process: "가공",
  labor: "노무비",
  purchased: "구매품",
  outsourced: "외주",
  logistics: "포장·물류",
  freight: "운임",
  other: "기타",
  credit: "환입(차감)",
};

/** The department that usually keeps a price of this kind up to date. */
export const CATEGORY_DEPARTMENT: Record<ItemCategory, string> = {
  material: "purchasing",
  process: "production_engineering",
  labor: "costing",
  purchased: "purchasing",
  outsourced: "purchasing",
  logistics: "logistics",
  freight: "logistics",
  other: "costing",
  credit: "costing",
};

const DECIMAL = /^\d+(\.\d+)?$/;
export const isDecimal = (v: string) => DECIMAL.test(v.trim());
export const isInt = (v: string) => /^\d+$/.test(v.trim());

/** "3" (percent) → "0.03"; anything but a decimal gives "". Exact (decimal.js), no float arithmetic. */
export function percentToFraction(percent: string): string {
  return isDecimal(percent) ? new Decimal(percent.trim()).div(100).toFixed() : "";
}

/** "0.03" → "3". */
export function fractionToPercent(fraction: string): string {
  return isDecimal(fraction) ? new Decimal(fraction.trim()).mul(100).toFixed() : "";
}

/** The last day of next year: a sensible default validity for a newly entered price. */
export function defaultValidUntil(today: string): string {
  return `${Number(today.slice(0, 4)) + 1}-12-31`;
}

/** The whole company record as `PUT /company` expects it, with `extra` rows appended. */
export function companyRequest(
  company: CompanyResponse["data"],
  extra: Omit<PriceRow, "id">[] = [],
): CompanyRequest {
  return {
    settings: company.settings,
    profile: company.profile,
    prices: [...company.prices, ...extra.map((r) => ({ ...r, id: null }))],
  };
}
