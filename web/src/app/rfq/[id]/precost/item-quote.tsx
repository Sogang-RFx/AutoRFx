"use client";

import { useState } from "react";
import Decimal from "decimal.js";
import type { PriceSheet } from "@/lib/rfq/contract";

type Price = { buy?: string; sell?: string };
const money = (value: Decimal | null) => value === null ? "—" : value.toFixed(2);
function extended(input: string | undefined, quantity: number) {
  return input && /^\d{1,10}(\.\d{1,2})?$/.test(input) ? new Decimal(input).times(quantity) : null;
}
function total(values: (Decimal | null)[]) {
  return values.reduce<Decimal | null>((sum, value) => sum !== null && value !== null ? sum.plus(value) : null, new Decimal(0));
}

export function ItemQuote({ sheet }: { sheet: PriceSheet }) {
  const [prices, setPrices] = useState<Record<number, Price>>({});
  const rows = sheet.items.map((row, index) => ({
    ...row, index, cost: extended(prices[index]?.buy, row.quantity), quote: extended(prices[index]?.sell, row.quantity),
  }));
  const categories = Array.from(new Set(rows.map(row => row.category)));
  const quote = total(rows.map(row => row.quote));
  const tax = quote && sheet.tax !== null ? quote.times(sheet.tax).div(100).toDecimalPlaces(2, Decimal.ROUND_HALF_UP) : null;
  const completed = rows.filter(row => row.quote !== null).length;

  function input(index: number, field: keyof Price, label: string) {
    const value = prices[index]?.[field] ?? "";
    const invalid = value !== "" && extended(value, 1) === null;
    return <input aria-label={label} aria-invalid={invalid} inputMode="decimal" value={value} placeholder="미입력"
      className="w-24 rounded-control border border-line-control p-2 text-right aria-invalid:border-red-600"
      onChange={event => {
        const next = event.target.value;
        setPrices(old => ({ ...old, [index]: { ...old[index], [field]: next } }));
      }} />;
  }

  return <section className="space-y-4 rounded-control border border-line bg-surface p-4">
    <h2 className="text-heading font-semibold">품목별 사전원가 · 견적</h2>
    <p>{sheet.source} · {sheet.revised ? "개정 견적표" : "견적표"} · {categories.length}개 분류 · {rows.length}개 품목</p>
    <p className="text-dense text-ink-muted">연간 예상 수량 기준입니다. USD 단가는 소수 둘째 자리까지 입력하세요. 각 합계는 해당 단가를 모두 입력하면 표시됩니다.</p>
    <div className="flex flex-wrap gap-4 rounded-control bg-accent-tint p-3">
      <strong>연간 매입원가 {money(total(rows.map(row => row.cost)))} USD</strong>
      <strong>견적 소계 {money(quote)} USD</strong>
      {sheet.tax !== null && <><span>판매세 ({sheet.tax}%) {money(tax)} USD</span><strong>세금 포함 견적 {money(quote && tax ? quote.plus(tax) : null)} USD</strong></>}
      <span>견적 입력 {completed}/{rows.length}</span>
    </div>
    {categories.map((category, group) => {
      const items = rows.filter(row => row.category === category);
      return <details key={category} open={group === 0} className="rounded-control border border-line p-3">
        <summary className="cursor-pointer font-semibold">{category} · {items.length}개 품목 · 매입 {money(total(items.map(row => row.cost)))} / 견적 {money(total(items.map(row => row.quote)))} USD</summary>
        <div className="mt-2 overflow-x-auto"><table className="w-full text-left text-dense">
          <thead><tr>{["품번 · 원문", "품명", "연간 수량", "매입단가", "견적단가", "연간 매입원가", "연간 견적액"].map(h => <th className="p-2" key={h}>{h}</th>)}</tr></thead>
          <tbody>{items.map(row => <tr key={row.index} className="border-t border-line">
            <td className="p-2">{row.part}{row.updated_part && <div>변경 {row.updated_part}</div>}<small className="block">#{row.item} · {row.page}쪽</small></td>
            <td className="p-2">{row.description}</td><td>{row.quantity}</td>
            <td>{input(row.index, "buy", row.part + " 매입단가")}</td><td>{input(row.index, "sell", row.part + " 견적단가")}</td>
            <td>{money(row.cost)}</td><td>{money(row.quote)}</td>
          </tr>)}</tbody>
        </table></div>
      </details>;
    })}
  </section>;
}