"use client";
/** Tables the user fills in: yearly volumes, cost items, and line items priced on their own. */
import { useState } from "react";
import { ITEM_CATEGORIES, type ItemCategory, type PriceRow, type RfqLine } from "@/lib/rfq/contract";
import { CATEGORY_LABEL, isDecimal, isInt, unitOptions } from "@/lib/rfq/precost";
import { newKey, type ItemRow, type LineRow, type VolumeRow } from "@/lib/rfq/precost-form";
import { Button, cx } from "../ui/primitives";
import { Field, input } from "./fields";

export function VolumesEditor({ rows, setRows, per }: { rows: VolumeRow[]; setRows: (rows: VolumeRow[]) => void; per: string }) {
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

export function ItemsEditor({
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

/**
 * Which price-sheet groups to price: the lines are filled from the RFQ, the user only adds prices.
 * `sheet` is every item of the chosen lists; at most `limit` are filled at a time, so a long
 * sheet (thousands of parts) is priced a few groups at a time.
 */
export function SheetPicker({ sheet, limit, apply }: { sheet: RfqLine[]; limit: number; apply: (rows: RfqLine[]) => void }) {
  const groups = [...new Set(sheet.map((r) => r.group ?? "기타"))];
  const unsized = sheet.filter((r) => r.quantity === null).length;
  // A long sheet starts with nothing chosen: the user picks the groups to fill.
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(sheet.length > limit ? [] : groups));
  const picked = sheet.filter((r) => chosen.has(r.group ?? "기타"));
  const pages = [...new Set(sheet.map((r) => r.page))].sort((a, b) => a - b);
  const files = new Set(sheet.map((r) => r.source_doc)).size;
  // Pages count per file: lists of two files (a PDF and an attached workbook) have no one page range.
  const where = files > 1 ? `파일 ${files}개` : `${pages[0]}${pages.length > 1 ? `–${pages[pages.length - 1]}` : ""}쪽`;
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
        <span className="font-semibold text-ink">RFQ 가격표에서 품목 {sheet.length}개를 찾았습니다</span> ({where}). 품목 이름과 수량은 RFQ에 적힌 그대로이고, 매입가만 넣으면 됩니다. 빠진 품목은 아래에서 직접 추가하세요(다시 채워도 유지됩니다).
        {unsized > 0 &&
          ` 이 중 ${unsized}개는 RFQ에 수량이 비어 있습니다(주문서에서 발주처가 적는 칸). 입찰할 품목의 물량을 직접 넣으세요.`}
        {sheet.length > limit &&
          ` 품목이 많아 한 번에 ${limit}개까지 채웁니다. 처음에는 앞의 ${limit}개를 채웠으니, 아래에서 분류를 골라 나눠 계산하세요.`}
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
        <Button variant="ghost" data-testid="sheet-apply" disabled={picked.length === 0} onClick={() => apply(picked.slice(0, limit))}>
          {picked.length > limit ? `선택한 ${picked.length}개 중 앞의 ${limit}개로 품목 다시 채우기` : `선택한 ${picked.length}개로 품목 다시 채우기`}
        </Button>
      </div>
    </div>
  );
}

export function LinesEditor({
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

export const MANUAL = "직접 추가";

/** A line the price-sheet reading missed, added by hand: name, part number, quantity and prices. */
export function AddLineForm({ currency, per, year, add }: { currency: string; per: string; year: string; add: (line: LineRow) => void }) {
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
