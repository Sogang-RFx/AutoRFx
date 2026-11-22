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

/** Currencies offered in the pickers. A currency the RFQ states that is not here is added to the list. */
export const CURRENCIES: { code: string; label: string }[] = [
  { code: "KRW", label: "KRW · 원" },
  { code: "USD", label: "USD · 미국 달러" },
  { code: "EUR", label: "EUR · 유로" },
  { code: "JPY", label: "JPY · 엔" },
  { code: "CNY", label: "CNY · 위안" },
  { code: "GBP", label: "GBP · 파운드" },
  { code: "CHF", label: "CHF · 스위스 프랑" },
  { code: "CZK", label: "CZK · 체코 코루나" },
  { code: "PLN", label: "PLN · 폴란드 즐로티" },
  { code: "HUF", label: "HUF · 헝가리 포린트" },
  { code: "MXN", label: "MXN · 멕시코 페소" },
  { code: "INR", label: "INR · 인도 루피" },
  { code: "VND", label: "VND · 베트남 동" },
  { code: "THB", label: "THB · 태국 바트" },
];

/** The picker options, with `extra` codes (from the RFQ or a saved row) kept when they are not in the list. */
export function currencyOptions(...extra: string[]): { code: string; label: string }[] {
  const known = new Set(CURRENCIES.map((c) => c.code));
  const more = [...new Set(extra.filter((c) => /^[A-Z]{3}$/.test(c) && !known.has(c)))];
  return [...CURRENCIES, ...more.map((code) => ({ code, label: code }))];
}

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
